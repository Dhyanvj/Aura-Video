import os
from typing import List, Optional

from app.agents.base import BaseAgent
from app.agents.schemas import CreativeBrief, ResearchDossier, SearchTermsRevision
from app.services import voice as voice_service
from app.utils import utils

# Shared between the initial script/terms pass and revise_search_terms() so a
# targeted revision gets the exact same term-quality guidance as the original
# draft, not a weaker ad-hoc version of it.
_SEARCH_TERM_GUIDANCE = """Provide 6-10 visual search terms, ordered to match the script's narrative order (each
term should correspond to what's being said around that point in the video) - this
feeds a "match materials to script" pipeline that downloads and places clips
sequentially, so order matters.

Search terms are matched against a real stock-footage library (Pexels) by fuzzy
keyword search, not by an LLM that understands intent - so a term with no concrete,
filmable subject will return generic or unrelated footage instead of erroring. To
avoid that:
- Every single term must name a concrete, literally filmable subject (e.g. the
  animal/object/person/place itself), not just an abstract concept. If the topic
  is scientific or abstract (anatomy, a process, a statistic), translate it into
  a concrete visible scene of the actual subject instead of the abstract idea -
  e.g. for "an octopus's three hearts", use "octopus close-up gills and mantle"
  or "octopus swimming underwater", never "heart anatomy diagram" (no stock
  footage shows an anatomy diagram of this animal).
- Never write a term like "<abstract noun> macro/close-up shot" with no concrete
  subject attached (e.g. "blue blood macro liquid") - stock libraries will
  return whatever generic macro footage loosely matches the adjectives, which is
  often unrelated and can occasionally be off-topic or inappropriate (e.g.
  colorful liquid/capsule photography). Anchor every term to the actual subject:
  "octopus with blue-tinted skin close-up", not "blue liquid macro".
- Prefer the subject's name literally present in most terms rather than implied.
- Be specific about species/variant when it matters to accuracy (e.g. "humpback
  whale breaching ocean" rather than just "whale", since a generic term can just
  as easily match an unrelated species like whale sharks or dolphins) and about
  setting when it matters (e.g. "wild open ocean" rather than a term that could
  match an aquarium or marina)."""

_SYSTEM_PROMPT = f"""You are the Creative Director for a short-form vertical video channel.
Write a hook-first script for a voiceover, with a STRICT MAXIMUM of 110-130 words (not
140-160): real narration with natural pauses between sentences runs slower than a raw
word-count-to-seconds estimate suggests, and this needs genuine margin under the 60-second
hard platform cap, not just a target that assumes zero pause time. If in doubt, write
shorter. Optimize for retention: an open loop or bold claim in the first 2 seconds, and a
payoff plus a short call-to-action at the end.

{_SEARCH_TERM_GUIDANCE}

Pick a music mood and, if one of the available BGM files fits, name it exactly as
given; otherwise leave bgm_file null and a random track will be used.

For voice_recommendation, copy one entry EXACTLY (character for character) from the
available_voices list you're given - it must be a real TTS voice ID, never a
description of a voice (e.g. never write something like "a deep calm narrator voice").

Suggest a subtitle style. Draft a working title and 3 hook variants for the metadata."""

_REVISE_TERMS_SYSTEM_PROMPT = f"""You are the Creative Director for a short-form vertical video channel.
The script, voice, and everything else about this video is already finalized and must
not change - a quality reviewer watched the rendered video and found that some of the
stock footage doesn't match what the script is saying at that point. Your only job here is to
produce a corrected set of search terms that will fetch footage that actually matches the
script, directly addressing the reviewer's feedback (e.g. if they said a clip showed the
wrong species or a captive/aquarium setting instead of the wild, make sure your new terms
rule that out explicitly).

{_SEARCH_TERM_GUIDANCE}"""

# Per-content-type structure, appended to _SYSTEM_PROMPT when the project has
# a content_type_id with a real addendum below. Content types not listed here
# (fun_facts, ai_news, world_news, trending_now) still use the base prompt
# unchanged - only Motivational has a distinct required structure so far.
_MOTIVATIONAL_ADDENDUM = """This video is for "Motivational Quotes & Life Lessons": build the entire script
around ONE specific, real quote (correctly attributed) OR ONE concrete, original life
lesson - never both, and never a vague collection of generic encouragement.

Follow this exact structure:
1. Hook (first ~2s): open on a relatable struggle the viewer will recognize in
   themselves - not the quote yet.
2. Centerpiece: state the quote or lesson clearly and completely, word for word, as its
   own moment in the script - this exact line will be shown on screen as large styled
   text, so it must be a single, complete, self-contained sentence (or two short
   sentences), not split across other narration.
3. Unpacking (2-3 sentences): explain concretely what the quote/lesson means in
   practice - a real behavior or choice, not more abstract encouragement.
4. Reflective closing line: land the point; don't append a generic "follow for more" -
   weave any call-to-action into this closing thought instead.

Populate quote_or_lesson:
- is_quote=true only if you're citing a real, well-documented quote from an identifiable
  person. Prefer quotes you're confident are both worded correctly AND correctly
  attributed - if you're not confident about the exact wording or who said it, set
  is_quote=false and write it as an original life lesson instead (no attribution needed).
  A wrong attribution is worse than no attribution.
- text must match, verbatim, the exact sentence(s) you wrote as the centerpiece in step 2.
- attribution is the person's name only (e.g. "Marcus Aurelius"), required when is_quote
  is true, omitted otherwise.
- attribution_confidence is your own honest self-assessment - "high" only if you're
  genuinely confident from training knowledge that this is both correctly worded and
  correctly attributed; "medium" or "low" otherwise. A quality reviewer rejects the video
  if this isn't "high" for a real quote, so default to a life lesson rather than guessing.

Keep visuals calm and cinematic, not high-energy or comedic."""

_CONTENT_TYPE_ADDENDA = {
    "motivational": _MOTIVATIONAL_ADDENDUM,
}
# Content types where quote_or_lesson must be populated on the brief - a
# missing centerpiece means the on-screen treatment (the whole point of the
# content type) can't be rendered.
_REQUIRES_QUOTE_OR_LESSON = {"motivational"}


class CreativeDirector(BaseAgent):
    agent_name = "creative_director"

    def write(
        self,
        topic: str,
        niche: str = "",
        revision_notes: Optional[str] = None,
        content_type_id: Optional[str] = None,
        research_dossier: Optional[ResearchDossier] = None,
    ) -> CreativeBrief:
        payload = {
            "topic": topic,
            "niche": niche,
            "available_bgm_files": self._list_bgm_files(),
            "available_voices": self._list_available_voices(),
        }
        system = _SYSTEM_PROMPT
        addendum = _CONTENT_TYPE_ADDENDA.get(content_type_id)
        if addendum:
            system += f"\n\n{addendum}"
        if research_dossier is not None:
            payload["verified_research"] = research_dossier.model_dump()
            system += (
                "\n\nA Researcher has already verified the following before you write anything - base "
                "your script's factual claims (and, if this content type has a quote/lesson centerpiece, "
                "that centerpiece) ONLY on this verified research; do not introduce a new unverified fact, "
                "quote, or attribution of your own. If the research has reduced_verification=true or lists "
                "disputed_points, be conservative - prefer phrasing that doesn't overstate certainty, or "
                "(for a quote/lesson type) fall back to an original life lesson rather than an unverified "
                "quote."
            )
        if revision_notes:
            payload["revision_notes"] = revision_notes
            system += (
                "\n\nThis is a revision. Address the following feedback from a prior "
                "quality review or human reviewer, while keeping what already worked:"
                f"\n{revision_notes}"
            )

        brief = self.call_json(system=system, user=utils.to_json(payload), response_model=CreativeBrief)

        if content_type_id in _REQUIRES_QUOTE_OR_LESSON and brief.quote_or_lesson is None:
            raise ValueError(
                f"Creative Director did not populate quote_or_lesson for content type "
                f"{content_type_id!r}, which requires an on-screen quote/lesson centerpiece"
            )

        return brief

    def revise_search_terms(self, script: str, niche: str, revision_notes: str) -> List[str]:
        """
        A cheaper, targeted revision for when QA found a visual/material
        mismatch but the script itself is fine: keeps the script (and its
        already-validated length/voice/pacing) frozen and only asks for new
        search terms, instead of discarding a working script to gamble on an
        entirely new one every time footage doesn't match.
        """
        payload = {"script": script, "niche": niche, "revision_notes": revision_notes}
        result = self.call_json(
            system=_REVISE_TERMS_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=SearchTermsRevision
        )
        return result.search_terms

    @staticmethod
    def _list_bgm_files() -> list[str]:
        song_dir = utils.song_dir()
        try:
            return [f for f in os.listdir(song_dir) if f.lower().endswith(".mp3")]
        except OSError:
            return []

    @staticmethod
    def _list_available_voices() -> list[str]:
        # Scripts are written in English, so offer English-locale voices only -
        # keeps the prompt compact instead of listing all 300+ Azure/Edge voices.
        return voice_service.get_all_azure_voices(filter_locals=["en-US", "en-GB", "en-AU"])
