from harness.redaction import REDACTED, redact, redact_text


def test_aws_key_redacted():
    out = redact_text("AKIAIOSFODNN7EXAMPLE here")
    assert "AKIA" not in out
    assert REDACTED in out


def test_github_token_redacted():
    out = redact_text("ghp_" + "a" * 40)
    assert "ghp_" not in out
    assert REDACTED in out


def test_authorization_header_value_redacted():
    src = 'Authorization: Bearer abcdef1234567890'
    out = redact_text(src)
    assert "abcdef" not in out
    assert REDACTED in out


def test_password_kv_redacted():
    out = redact_text("password=hunter2-very-secret")
    assert "hunter2" not in out
    assert REDACTED in out


def test_recursive_redaction_in_dict_list():
    payload = {
        "logs": ["AKIAIOSFODNN7EXAMPLE in log line", "boring line"],
        "headers": {"Authorization": "Bearer secret-xyz-abc"},
    }
    out = redact(payload)
    flat = str(out)
    assert "AKIA" not in flat
    assert "secret-xyz-abc" not in flat


def test_private_key_block_redacted():
    src = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    out = redact_text(src)
    assert "abc" not in out
