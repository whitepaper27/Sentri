"""Test policy file loader and markdown parser."""

from sentri.policy.loader import parse_policy_md


def test_parse_frontmatter():
    content = """---
type: alert_pattern
name: test
version: 1
---

# Test Alert
"""
    result = parse_policy_md(content)
    assert result["frontmatter"]["type"] == "alert_pattern"
    assert result["frontmatter"]["name"] == "test"
    assert result["frontmatter"]["version"] == 1


def test_parse_sections():
    content = """---
type: test
---

## First Section

Some text here.

## Second Section

More text.
"""
    result = parse_policy_md(content)
    assert "first_section" in result
    assert "second_section" in result


def test_parse_code_blocks():
    content = """---
type: test
---

## Email Pattern

```regex
(?i)tablespace\\s+(\\S+)
```

## Forward Action

```sql
ALTER TABLESPACE :name ADD DATAFILE
```
"""
    result = parse_policy_md(content)
    email_section = result.get("email_pattern", {})
    assert "regex" in email_section

    action_section = result.get("forward_action", {})
    assert "sql" in action_section


def test_parse_bullet_lists():
    content = """---
type: test
---

## Extracted Fields

- tablespace_name: group(1)
- used_percent: group(2)
- database_id: group(3)
"""
    result = parse_policy_md(content)
    section = result.get("extracted_fields", {})
    assert "items" in section
    assert len(section["items"]) == 3
    assert "tablespace_name: group(1)" in section["items"]


def test_load_alert_policy(policy_loader):
    """Test loading a real alert policy file."""
    policy = policy_loader.load_alert("tablespace_full")
    assert policy  # Not empty
    assert "frontmatter" in policy
    assert policy["frontmatter"].get("name") == "tablespace_full"


def test_load_all_alerts(policy_loader):
    """Test loading all alert policies."""
    alerts = policy_loader.load_all_alerts()
    assert len(alerts) >= 5
    assert "tablespace_full" in alerts
    assert "archive_dest_full" in alerts
    assert "temp_full" in alerts
    assert "listener_down" in alerts
    assert "archive_gap" in alerts


def test_load_brain_policy(policy_loader):
    """Test loading a brain policy file."""
    policy = policy_loader.load_brain("global_policy")
    assert policy
    assert "frontmatter" in policy


def test_cache_and_reload(policy_loader):
    """Test that cache works and reload clears it."""
    policy1 = policy_loader.load_alert("tablespace_full")
    policy2 = policy_loader.load_alert("tablespace_full")
    assert policy1 is policy2  # Same cached object

    policy_loader.reload()
    policy3 = policy_loader.load_alert("tablespace_full")
    assert policy3 is not policy1  # Fresh load
