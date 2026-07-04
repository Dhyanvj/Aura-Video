import os
from typing import Optional

from app.agents.base import BaseAgent
from app.agents.schemas import CreativeBrief
from app.services import voice as voice_service
from app.utils import utils

_SYSTEM_PROMPT = """You are the Creative Director for a short-form vertical video channel.
Write a hook-first script, at most 60 seconds spoken aloud (about 140-160 words),
optimized for retention: an open loop or bold claim in the first 2 seconds, and a
payoff plus a short call-to-action at the end.

Provide 6-10 visual search terms, ordered to match the script's narrative order (each
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

Pick a music mood and, if one of the available BGM files fits, name it exactly as
given; otherwise leave bgm_file null and a random track will be used.

For voice_recommendation, copy one entry EXACTLY (character for character) from the
available_voices list you're given - it must be a real TTS voice ID, never a
description of a voice (e.g. never write something like "a deep calm narrator voice").

Suggest a subtitle style. Draft a working title and 3 hook variants for the metadata."""


class CreativeDirector(BaseAgent):
    agent_name = "creative_director"

    def write(self, topic: str, niche: str = "", revision_notes: Optional[str] = None) -> CreativeBrief:
        payload = {
            "topic": topic,
            "niche": niche,
            "available_bgm_files": self._list_bgm_files(),
            "available_voices": self._list_available_voices(),
        }
        system = _SYSTEM_PROMPT
        if revision_notes:
            payload["revision_notes"] = revision_notes
            system += (
                "\n\nThis is a revision. Address the following feedback from a prior "
                "quality review or human reviewer, while keeping what already worked:"
                f"\n{revision_notes}"
            )

        return self.call_json(system=system, user=utils.to_json(payload), response_model=CreativeBrief)

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
