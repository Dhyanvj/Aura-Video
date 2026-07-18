from typing import List, Optional

from app.agents.base import BaseAgent
from app.agents.schemas import ResearchDossier
from app.services import news_sources
from app.utils import utils

_BASE_INSTRUCTIONS = """You are the Researcher for a short-form vertical video channel. Use the web_search
tool to gather real, current information before any script gets written - nothing downstream should state a
fact you haven't actually checked.

Always look for at least 2 independent sources for anything you'll present as a verified fact. If you can't
find 2 independent sources, say so plainly rather than presenting a single-source claim as solid. Avoid the
topics listed as "recent_topics_to_avoid" - the channel has already covered those.

If "supplementary_signals" or "fact_check_signals" are given, they're free, non-authoritative leads (recent
headlines, or an existing fact-check verdict on a similar claim) to help you search more precisely and cross-
check freshness/accuracy - always verify them yourself via web_search rather than trusting them at face value;
they're a starting point, not a source you can cite directly."""

_MOTIVATIONAL_PROMPT = f"""{_BASE_INSTRUCTIONS}

Find ONE specific, well-documented quote suited to the niche/theme given, OR decide a real quote is too risky
to verify and recommend an original life lesson instead (no attribution needed - safer than a wrong one). For
a quote: confirm both the exact wording and the correct author from at least 2 independent sources. If sources
disagree on wording or attribution, or you only find one source, do NOT present it as verified - either keep
searching for corroboration or recommend a life lesson instead.

Write up what you recommend (the quote or lesson, quoted exactly), why it fits the niche/theme, and the
sources you checked with their URLs."""

_FUN_FACTS_PROMPT = f"""{_BASE_INSTRUCTIONS}

Find ONE specific, surprising, and verifiable fact suited to the niche/theme given. Confirm it from at least 2
independent sources. Explicitly check whether this is a commonly repeated myth or debunked claim (e.g. "humans
only use 10% of their brain," "goldfish have a 3-second memory") - if your search turns up debunking sources,
reject that fact entirely and find a different, genuinely true one instead. Never present a myth as fact just
because it's popular or "commonly known."

Write up the fact you recommend, why it's surprising, and the sources you checked with their URLs (including
anything that debunks a related myth, if relevant)."""

_NEWS_PROMPT_TEMPLATE = f"""{_BASE_INSTRUCTIONS}

Find ONE specific, current news story (not a vague roundup of several stories) published within the last
{{freshness_window_hours}} hours, suited to the niche/theme given. Confirm the key facts (what happened, who,
when) from at least 2 independent sources. Note the exact publication date/time of each source so freshness
can be checked. If you can't find a genuinely recent, well-corroborated story, say so plainly rather than
presenting an older or single-source story as current news.

Write up the story, why it matters right now, and the sources you checked with their URLs and publication
dates."""

_GENERIC_PROMPT = f"""{_BASE_INSTRUCTIONS}

Find and verify one specific, concrete topic suited to the niche/theme given, confirmed from at least 2
independent sources.

Write up what you recommend, why it fits, and the sources you checked with their URLs."""

_SUPPLEMENT_PROMPT = """A quality reviewer flagged something in a video script as not supported by your earlier
research dossier for this topic. Use the web_search tool to specifically try to verify it now - search harder
or from different angles than your first pass. If you can now confirm it (exact wording + attribution for a
quote, or the fact/claim) from at least 2 independent sources, say so plainly and cite them. If you still
cannot verify it after searching, or your search actively contradicts it, say that plainly too - do not guess
or invent a source just to resolve the flag."""

_STRUCTURE_SYSTEM_PROMPT = """Structure the research notes below into the required schema. Only include facts
and sources that actually appear in the notes - never invent a URL, title, or fact that isn't there. If the
notes say something couldn't be verified, is disputed, or is a myth to avoid, reflect that honestly via
confidence/disputed_points rather than smoothing it over. `topic` should be the specific thing you're
recommending (the quote, the fact, or the news story headline) - concrete enough to become a video's subject,
not the general niche/theme it came from.

If (and only if) the notes recommend ONE specific, real, attributed quote (motivational-type research),
populate `verified_quote` with its exact wording and attribution: verification_status="verified" only if the
notes confirm it from >=2 independent sources with no disagreement; "disputed" if the notes say sources
conflict or debunk it; "unverified" if the notes could only find one source or recommend a life lesson instead
(in that case, do not invent a quote to fill this field - leave it unset). This is what downstream QA trusts
instead of re-deriving its own opinion of the quote's accuracy, so it must accurately reflect what the notes
actually found - never mark "verified" as a formality."""


class Researcher(BaseAgent):
    agent_name = "researcher"

    def research(
        self,
        content_type_id: str,
        topic_hint: str = "",
        niche: str = "",
        audience: str = "",
        recent_topics: Optional[List[str]] = None,
        performance_notes: Optional[List[str]] = None,
        freshness_window_hours: Optional[int] = None,
    ) -> ResearchDossier:
        """
        Runs a per-content-type research pass and returns a ResearchDossier.
        On any failure (missing key, API error, web search unavailable, or
        no usable text produced) returns a dossier with
        reduced_verification=True instead of raising - callers decide what
        that means for their content type (e.g. news types treat it as an
        automatic QA fail; others can proceed more cautiously).
        """
        system = self._research_prompt(content_type_id, freshness_window_hours)
        payload = {
            "content_type": content_type_id,
            "topic_hint": topic_hint,
            "niche": niche,
            "audience": audience,
            "recent_topics_to_avoid": recent_topics or [],
            "past_performance_notes": performance_notes or [],
        }
        self._add_supplementary_signals(payload, content_type_id, topic_hint or niche)
        summary, sources, ok = self.call_with_web_search(system=system, user=utils.to_json(payload))

        if not ok:
            self.log_event(
                "error",
                message="Research produced no usable results; marking reduced_verification",
            )
            return ResearchDossier(
                topic=topic_hint or niche or "unspecified",
                why_now="Research unavailable - web search failed or returned nothing usable.",
                freshness_window_hours=freshness_window_hours,
                reduced_verification=True,
            )

        return self._structure_dossier(topic_hint, summary, sources, freshness_window_hours)

    def supplement_verification(self, topic: str, flagged_item: str) -> ResearchDossier:
        """
        Incident fix §4: a QA finding whose fix is evidence, not rewriting
        (attribution uncertainty, a missing citation) routes here for ONE
        focused re-verification attempt before ever looping back to the
        Creative Director - who can only reword, not confirm a fact. Returns
        a dossier built from this focused pass alone; the orchestrator merges
        it into the project's existing dossier (see
        orchestrator._merge_supplementary_dossier) rather than replacing it
        outright, so unrelated already-verified facts survive.
        """
        payload = {"topic": topic, "flagged_item": flagged_item}
        summary, sources, ok = self.call_with_web_search(system=_SUPPLEMENT_PROMPT, user=utils.to_json(payload))
        if not ok:
            self.log_event(
                "error",
                message="Supplementary verification produced no usable results; flagged item stays unverified",
            )
            return ResearchDossier(
                topic=topic,
                why_now="Supplementary verification could not confirm the flagged item.",
                reduced_verification=True,
            )
        return self._structure_dossier(topic, summary, sources, None)

    def _add_supplementary_signals(self, payload: dict, content_type_id: str, query: str) -> None:
        """
        docs/DECISIONS_V3.md §6: free, additive leads alongside the primary
        Anthropic web-search call - never the sole source, and never allowed
        to block/slow the research call itself if they fail (each already
        degrades to [] internally; this is just choosing which free sources
        apply to which content type).
        """
        if not query:
            return
        if content_type_id == "ai_news":
            signals = news_sources.fetch_ai_news_signals(query)
            if signals:
                payload["supplementary_signals"] = signals
                self.log_event("tool_call", message=f"Hacker News/RSS supplement: {len(signals)} signal(s)")
        elif content_type_id == "world_news":
            signals = news_sources.fetch_gdelt_articles(query)
            if signals:
                payload["supplementary_signals"] = signals
                self.log_event("tool_call", message=f"GDELT supplement: {len(signals)} signal(s)")

        if news_sources.is_fact_check_configured():
            fact_checks = news_sources.fetch_fact_checks(query)
            if fact_checks:
                payload["fact_check_signals"] = fact_checks
                self.log_event("tool_call", message=f"Google Fact Check supplement: {len(fact_checks)} signal(s)")

    def _research_prompt(self, content_type_id: str, freshness_window_hours: Optional[int]) -> str:
        if content_type_id == "motivational":
            return _MOTIVATIONAL_PROMPT
        if content_type_id == "fun_facts":
            return _FUN_FACTS_PROMPT
        if content_type_id in ("ai_news", "world_news"):
            return _NEWS_PROMPT_TEMPLATE.format(freshness_window_hours=freshness_window_hours or 24)
        return _GENERIC_PROMPT

    def _structure_dossier(
        self,
        topic_hint: str,
        summary: str,
        sources: List[dict],
        freshness_window_hours: Optional[int],
    ) -> ResearchDossier:
        payload = {
            "topic_hint": topic_hint,
            "research_notes": summary,
            "sources_found": sources,
            "freshness_window_hours": freshness_window_hours,
        }
        dossier = self.call_json(
            system=_STRUCTURE_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=ResearchDossier
        )
        # Reflects the constraint actually applied to this research pass,
        # not whatever (if anything) the model echoed back into the schema.
        dossier.freshness_window_hours = freshness_window_hours
        return dossier
