"""Judge: LLM-based consensus system for evaluating improvement proposals.

Uses M/N agreement: a configurable number of LLM judges must agree
that a proposed change is safe and beneficial before it's applied.

v2.0: Supports diverse multi-provider judging — each judge can use a
different LLM (Claude, OpenAI, Gemini) for genuine consensus diversity
instead of asking the same model N times.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sentri.core.llm_interface import LLMProvider
from sentri.llm.prompts import JUDGE_SYSTEM_PROMPT

logger = logging.getLogger("sentri.learning.judge")


class JudgePanel:
    """Runs M/N LLM judge consensus on improvement proposals.

    Accepts a list of providers for diverse consensus.  When multiple
    providers are available, each judge uses a different LLM (round-robin).
    Falls back to repeated calls on a single provider when only one is given.
    """

    def __init__(
        self,
        llm_providers: Optional[list[LLMProvider]] = None,
        judge_count: int = 3,
        required_agreement: int = 2,
    ):
        # Keep only providers that are actually available
        self._providers: list[LLMProvider] = [
            p for p in (llm_providers or []) if p and p.is_available()
        ]
        self._judge_count = judge_count
        self._required_agreement = required_agreement

    def evaluate(self, proposal: dict) -> dict:
        """Evaluate a proposal through the judge panel.

        Returns:
            {
                "approved": bool,
                "votes": [{"approved": bool, "reasoning": str, ...}, ...],
                "agreement_count": int,
                "total_judges": int,
                "diverse": bool,  # True if multiple providers used
            }
        """
        if not self._providers:
            logger.info("No LLM available for judge panel — requires human review")
            return {
                "approved": False,
                "votes": [],
                "agreement_count": 0,
                "total_judges": 0,
                "diverse": False,
                "reason": "No LLM available — requires human review",
            }

        diverse = len(self._providers) > 1

        votes = []
        for i in range(self._judge_count):
            # Round-robin across available providers
            provider = self._providers[i % len(self._providers)]
            vote = self._run_single_judge(proposal, judge_number=i + 1, llm=provider)
            votes.append(vote)

        approvals = sum(1 for v in votes if v.get("approved", False))

        result = {
            "approved": approvals >= self._required_agreement,
            "votes": votes,
            "agreement_count": approvals,
            "total_judges": self._judge_count,
            "diverse": diverse,
        }

        providers_used = list({v.get("provider", "?") for v in votes})
        logger.info(
            "Judge panel result: %d/%d approved (need %d) — %s [providers: %s]",
            approvals,
            self._judge_count,
            self._required_agreement,
            "APPROVED" if result["approved"] else "REJECTED",
            ", ".join(providers_used),
        )

        return result

    def _run_single_judge(self, proposal: dict, judge_number: int, llm: LLMProvider) -> dict:
        """Run a single judge evaluation using the given LLM provider."""
        prompt = (
            f"Judge #{judge_number}: Evaluate this proposed improvement.\n\n"
            f"Alert Type: {proposal.get('alert_type', 'unknown')}\n"
            f"Section to Modify: {proposal.get('section', 'unknown')}\n"
            f"Reasoning: {proposal.get('reasoning', 'none')}\n"
            f"Proposed Change: {proposal.get('proposed_content', 'none')}\n"
            f"Evidence/Patterns: {json.dumps(proposal.get('patterns', {}))}\n\n"
            "Is this change technically correct, safe, and beneficial?"
        )

        try:
            raw = llm.generate(
                prompt=prompt,
                system_prompt=JUDGE_SYSTEM_PROMPT,
                temperature=0.1 + (judge_number * 0.1),  # Vary temperature
                max_tokens=512,
                json_mode=True,
            )

            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines)

            vote = json.loads(text)
            vote["judge_number"] = judge_number
            vote["provider"] = llm.name
            return vote

        except Exception as e:
            logger.warning("Judge #%d (%s) failed: %s", judge_number, llm.name, e)
            return {
                "approved": False,
                "reasoning": f"Judge evaluation failed: {e}",
                "confidence": 0.0,
                "concerns": ["Evaluation error"],
                "judge_number": judge_number,
                "provider": llm.name,
            }
