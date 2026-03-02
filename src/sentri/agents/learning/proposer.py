"""Proposer: generates improvement proposals from accumulated observations.

Analyzes patterns across observations for a given alert type and proposes
changes to the .md policy files (e.g., adjusting tolerance, adding checks).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sentri.core.llm_interface import LLMProvider
from sentri.db.learning_repo import LearningRepository

logger = logging.getLogger("sentri.learning.proposer")

PROPOSER_SYSTEM_PROMPT = """\
You are an expert Oracle DBA reviewing operational data from an automated
remediation system. Based on the observations provided, propose a specific
improvement to the alert policy (.md) file.

Rules:
1. Only propose changes supported by clear evidence in the observations.
2. Each proposal must include the exact section to modify and the new content.
3. Be conservative — prefer small, safe improvements.
4. Common improvements: adjust tolerance thresholds, add pre-flight checks,
   refine verification queries, update risk levels.

Respond with ONLY a JSON object:
{
  "section": "The .md section to modify (e.g., 'tolerance', 'pre_flight_checks')",
  "current_content": "Brief summary of current content",
  "proposed_content": "The exact new content for this section",
  "reasoning": "Why this change improves the policy",
  "evidence": "What observations support this change",
  "confidence": 0.0-1.0
}
"""


class Proposer:
    """Generates improvement proposals from observation patterns."""

    def __init__(
        self,
        learning_repo: LearningRepository,
        llm_provider: Optional[LLMProvider] = None,
        min_observations: int = 5,
    ):
        self._repo = learning_repo
        self._llm = llm_provider
        self._min_observations = min_observations

    def check_and_propose(self, alert_type: str) -> Optional[dict]:
        """Check if enough observations exist to propose an improvement.

        Returns a proposal dict if one is generated, None otherwise.
        """
        observations = self._repo.find_by_alert_type(alert_type)

        if len(observations) < self._min_observations:
            logger.debug(
                "Not enough observations for %s (%d/%d)",
                alert_type,
                len(observations),
                self._min_observations,
            )
            return None

        # Analyze patterns
        patterns = self._analyze_patterns(observations)

        if not patterns.get("has_actionable_pattern"):
            return None

        # Generate proposal via LLM if available
        if self._llm and self._llm.is_available():
            return self._generate_llm_proposal(alert_type, patterns)

        # Without LLM, generate rule-based proposals
        return self._generate_rule_based_proposal(alert_type, patterns)

    def _analyze_patterns(self, observations) -> dict:
        """Analyze observations to find actionable patterns."""
        total = len(observations)
        successes = 0
        failures = 0
        rollbacks = 0
        false_positives = 0
        preflight_failures = 0

        for obs in observations:
            if obs.observation_type == "EXECUTION_SUCCESS":
                successes += 1
            elif obs.observation_type == "EXECUTION_FAILURE":
                failures += 1
            elif obs.observation_type == "ROLLBACK":
                rollbacks += 1
            elif obs.observation_type == "FALSE_POSITIVE":
                false_positives += 1
            elif obs.observation_type == "PRE_FLIGHT_FAILURE":
                preflight_failures += 1

        success_rate = successes / max(total, 1)
        failure_rate = failures / max(total, 1)
        false_positive_rate = false_positives / max(total, 1)
        rollback_rate = rollbacks / max(total, 1)

        has_actionable = failure_rate > 0.2 or false_positive_rate > 0.3 or rollback_rate > 0.15

        return {
            "total": total,
            "successes": successes,
            "failures": failures,
            "rollbacks": rollbacks,
            "false_positives": false_positives,
            "preflight_failures": preflight_failures,
            "success_rate": round(success_rate, 3),
            "failure_rate": round(failure_rate, 3),
            "false_positive_rate": round(false_positive_rate, 3),
            "rollback_rate": round(rollback_rate, 3),
            "has_actionable_pattern": has_actionable,
        }

    def _generate_llm_proposal(self, alert_type: str, patterns: dict) -> Optional[dict]:
        """Use LLM to generate an improvement proposal."""
        observations = self._repo.find_by_alert_type(alert_type)
        obs_summaries = []
        for obs in observations[-20:]:  # Last 20 observations
            try:
                data = json.loads(obs.data)
                obs_summaries.append(
                    {
                        "type": obs.observation_type,
                        "database": obs.database_id,
                        "data": data,
                    }
                )
            except json.JSONDecodeError:
                pass

        prompt = (
            f"Alert Type: {alert_type}\n\n"
            f"Pattern Analysis:\n{json.dumps(patterns, indent=2)}\n\n"
            f"Recent Observations:\n{json.dumps(obs_summaries, indent=2)}\n\n"
            "Based on these patterns, propose one specific improvement to "
            "the alert policy file."
        )

        try:
            raw = self._llm.generate(
                prompt=prompt,
                system_prompt=PROPOSER_SYSTEM_PROMPT,
                temperature=0.2,
                max_tokens=1024,
            )

            # Parse response
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines)

            proposal = json.loads(text)
            proposal["alert_type"] = alert_type
            proposal["patterns"] = patterns
            proposal["source"] = "llm"
            return proposal

        except Exception as e:
            logger.warning("LLM proposal generation failed: %s", e)
            return self._generate_rule_based_proposal(alert_type, patterns)

    def _generate_rule_based_proposal(self, alert_type: str, patterns: dict) -> Optional[dict]:
        """Generate a simple rule-based proposal without LLM."""
        # High false positive rate -> suggest tightening tolerance
        if patterns["false_positive_rate"] > 0.3:
            return {
                "alert_type": alert_type,
                "section": "tolerance",
                "reasoning": (
                    f"False positive rate is {patterns['false_positive_rate']:.0%} "
                    f"({patterns['false_positives']}/{patterns['total']} observations). "
                    "Consider tightening the verification tolerance."
                ),
                "proposed_content": "Reduce tolerance margin by 50%",
                "confidence": 0.6,
                "patterns": patterns,
                "source": "rule_based",
            }

        # High rollback rate -> suggest adjusting risk level
        if patterns["rollback_rate"] > 0.15:
            return {
                "alert_type": alert_type,
                "section": "risk_level",
                "reasoning": (
                    f"Rollback rate is {patterns['rollback_rate']:.0%} "
                    f"({patterns['rollbacks']}/{patterns['total']} observations). "
                    "Consider raising the risk level."
                ),
                "proposed_content": "Raise risk level to HIGH",
                "confidence": 0.5,
                "patterns": patterns,
                "source": "rule_based",
            }

        # High failure rate -> suggest adding pre-flight checks
        if patterns["failure_rate"] > 0.2:
            return {
                "alert_type": alert_type,
                "section": "pre_flight_checks",
                "reasoning": (
                    f"Failure rate is {patterns['failure_rate']:.0%} "
                    f"({patterns['failures']}/{patterns['total']} observations). "
                    "Consider adding additional pre-flight checks."
                ),
                "proposed_content": "Add validation for common failure conditions",
                "confidence": 0.5,
                "patterns": patterns,
                "source": "rule_based",
            }

        return None
