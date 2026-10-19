"""
Tests for secret scrubbing regexes and on-demand redaction (redactor.py).
"""

import time

import pytest

from app.core.redactor import MAX_REDACT_LINE_CHARS, REDACTED, TRUNCATION_MARKER, redact


class TestRedactor:

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx.yyy"
        result = redact(text)
        assert "eyJ" not in result
        assert REDACTED in result
        assert "Authorization" in result

    def test_basic_auth_redacted(self):
        text = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        result = redact(text)
        assert "dXNlcjpwYXNzd29yZA==" not in result
        assert REDACTED in result

    def test_api_key_redacted(self):
        text = "api_key=sk-1234567890abcdef1234567890abcdef"
        result = redact(text)
        assert "sk-1234567890abcdef" not in result
        assert REDACTED in result

    def test_api_key_colon_format(self):
        text = "API-KEY: my_super_secret_key_value_12345678"
        result = redact(text)
        assert "my_super_secret_key_value_12345678" not in result
        assert REDACTED in result

    def test_password_field_redacted(self):
        text = "password=MyS3cretP@ss!"
        result = redact(text)
        assert "MyS3cretP@ss!" not in result
        assert REDACTED in result

    def test_password_colon_format(self):
        text = "password: hunter2"
        result = redact(text)
        assert "hunter2" not in result
        assert REDACTED in result

    def test_token_field_redacted(self):
        text = 'access_token=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        result = redact(text)
        assert "ghp_" not in result
        assert REDACTED in result

    def test_jwt_standalone_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"Token is {jwt}"
        result = redact(text)
        assert "eyJhbGciOiJIUzI1NiI" not in result
        assert REDACTED in result

    def test_aws_access_key_redacted(self):
        text = "AWS key is AKIAIOSFODNN7EXAMPLE"
        result = redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert REDACTED in result

    def test_aws_secret_key_redacted(self):
        text = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY12"
        result = redact(text)
        assert "wJalrXUtnFEMI" not in result
        assert REDACTED in result

    def test_private_key_redacted(self):
        text = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO...
-----END RSA PRIVATE KEY-----"""
        result = redact(text)
        assert "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO" not in result
        assert REDACTED in result

    def test_connection_string_password_redacted(self):
        text = "postgres://admin:s3cret_pass@db.example.com:5432/mydb"
        result = redact(text)
        assert "s3cret_pass" not in result
        assert REDACTED in result
        assert "db.example.com" in result

    def test_connection_string_with_at_symbol_in_password(self):
        text = "Connecting to postgres://postgres:P@ssw0rd123!@db.internal:5432/prod_db with api_key=sk-proj-9999888877776666555544443333"
        result = redact(text)
        assert "P@ssw0rd123!" not in result
        assert "@ssw0rd123!" not in result
        assert result == f"Connecting to postgres://postgres:{REDACTED}@db.internal:5432/prod_db with api_key={REDACTED}"

    def test_pushover_token_redacted(self):
        text = "pushover_app_token=appToken1234567890abcdefghijklm"
        result = redact(text)
        assert "appToken1234567890abcdefghijklm" not in result
        assert REDACTED in result

    def test_normal_text_unchanged(self):
        text = "INFO: Container nginx started successfully on port 8080"
        result = redact(text)
        assert result == text

    def test_redact_list(self):
        lines = [
            "Normal log line",
            "api_key=secret12345678",
            "password=hunter2",
        ]
        result = redact(lines)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0] == "Normal log line"
        assert "secret12345678" not in result[1]
        assert "hunter2" not in result[2]

    def test_multiple_secrets_in_one_line(self):
        text = "Authorization: Bearer tok123abc password=mypass api_key=sk-abc12345678"
        result = redact(text)
        assert "tok123abc" not in result
        assert "mypass" not in result
        assert "sk-abc12345678" not in result

    def test_x_api_key_header(self):
        text = "X-API-Key: my-secret-api-key-12345"
        result = redact(text)
        assert "my-secret-api-key-12345" not in result
        assert REDACTED in result

    def test_redis_connection_string_without_username_redacted(self):
        text = "Connecting to redis://:mypassword@localhost:6379/0..."
        result = redact(text)
        assert "mypassword" not in result
        assert REDACTED in result
        assert result == f"Connecting to redis://:{REDACTED}@localhost:6379/0..."

    def test_sk_token_standalone_redacted(self):
        token = "sk-proj-abc1234567890abcdef12345678"
        text = f"API request using OpenAI key {token} failed"
        result = redact(text)
        assert token not in result
        assert REDACTED in result
        assert result == f"API request using OpenAI key {REDACTED} failed"

    def test_github_pat_standalone_redacted(self):
        # ghp_ followed by exactly 36 alphanumeric characters (total length 40)
        token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        assert len(token) == 40
        text = f"git clone error with token {token}"
        result = redact(text)
        assert token not in result
        assert REDACTED in result
        assert result == f"git clone error with token {REDACTED}"

    def test_curl_auth_redacted(self):
        text1 = "curl -u admin:s3cretPassword123 https://api.internal/v1"
        result1 = redact(text1)
        assert "s3cretPassword123" not in result1
        assert result1 == f"curl -u admin:{REDACTED} https://api.internal/v1"

        text2 = "curl --user deploy:verySecretKey456 https://api.internal/v1"
        result2 = redact(text2)
        assert "verySecretKey456" not in result2
        assert result2 == f"curl --user deploy:{REDACTED} https://api.internal/v1"

    def test_slack_webhook_redacted(self):
        url = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
        text = f"Sending alert to {url} failed with status 500"
        result = redact(text)
        assert "T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX" not in result
        assert REDACTED in result
        assert "https://hooks.slack.com/services/[REDACTED]" in result

    def test_generic_secret_assignment_redacted(self):
        text_double = 'secret="my_super_secret_double_quotes"'
        result_double = redact(text_double)
        assert "my_super_secret_double_quotes" not in result_double
        assert REDACTED in result_double

        text_single = "secret='my_super_secret_single_quotes'"
        result_single = redact(text_single)
        assert "my_super_secret_single_quotes" not in result_single
        assert REDACTED in result_single

        text_client = 'client_secret="oauth2_client_secret_xyz"'
        result_client = redact(text_client)
        assert "oauth2_client_secret_xyz" not in result_client
        assert REDACTED in result_client

        text_unquoted = "secret=my_unquoted_secret_value"
        result_unquoted = redact(text_unquoted)
        assert "my_unquoted_secret_value" not in result_unquoted
        assert REDACTED in result_unquoted

    def test_sshpass_command_redacted(self):
        text1 = "sshpass -p 'MyS3cretPassword123' ssh user@192.168.1.10"
        result1 = redact(text1)
        assert "MyS3cretPassword123" not in result1
        assert REDACTED in result1
        assert result1 == f"sshpass -p {REDACTED} ssh user@192.168.1.10"

        text2 = 'sshpass -p "AnotherSecretPass" scp file.txt host:/tmp'
        result2 = redact(text2)
        assert "AnotherSecretPass" not in result2
        assert REDACTED in result2
        assert result2 == f"sshpass -p {REDACTED} scp file.txt host:/tmp"

        text3 = "ssh -p 22 user@host"
        result3 = redact(text3)
        assert result3 == text3

    def test_docker_registry_auth_redacted(self):
        text_header = "X-Registry-Auth: eyJ1c2VybmFtZSI6ImFkbWluIiwicGFzc3dvcmQiOiJzZWNyZXQifQ=="
        result_header = redact(text_header)
        assert "eyJ1c2VybmFtZSI6ImFkbWluIiwicGFzc3dvcmQiOiJzZWNyZXQifQ==" not in result_header
        assert REDACTED in result_header

        text_token = "X-Registry-Token: reg_token_abcdef123456"
        result_token = redact(text_token)
        assert "reg_token_abcdef123456" not in result_token
        assert REDACTED in result_token

        text_var = "docker_registry_token=reg_token_value_999"
        result_var = redact(text_var)
        assert "reg_token_value_999" not in result_var
        assert REDACTED in result_var



class TestRedactorQuotedKeys:
    """S-03: key/value patterns must also match JSON and Python style quoted keys."""

    def test_json_password_redacted(self):
        result = redact('{"password":"hunter2"}')
        assert "hunter2" not in result
        assert REDACTED in result

    def test_json_password_with_spaces_in_value_redacted(self):
        result = redact('{"user": "bob", "password": "correct horse battery staple"}')
        assert "correct" not in result
        assert "staple" not in result
        assert '"user": "bob"' in result

    def test_nested_json_api_key_redacted(self):
        result = redact('{"auth":{"api_key":"abcd1234efgh5678"}}')
        assert "abcd1234efgh5678" not in result
        assert REDACTED in result

    def test_single_quoted_keys_redacted(self):
        result = redact("{'password': 'hunter2', 'access_token': 'tok_abcdef123456'}")
        assert "hunter2" not in result
        assert "tok_abcdef123456" not in result

    def test_key_with_spaces_before_colon_redacted(self):
        result = redact('"password"   :   "hunter2"')
        assert "hunter2" not in result
        result = redact("password    : hunter2")
        assert "hunter2" not in result

    def test_escaped_json_in_log_message_redacted(self):
        text = r'payload={\"username\":\"bob\",\"password\":\"my secret pass\",\"secret\":\"abc def\"}'
        result = redact(text)
        assert "my secret pass" not in result
        assert "secret pass" not in result
        assert "abc def" not in result
        assert "bob" in result

    def test_json_token_and_secret_keys_redacted(self):
        text = (
            '{"token": "abcdefgh12345678", "client_secret": "cs_value_123", '
            '"aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY12", '
            '"pushover_user_key": "uQiRzpo4DXghDmr9QzzfQu27cmVRsG", '
            '"X-Registry-Auth": "eyJ1c2VybmFtZSI6ImFkbWluIn0="}'
        )
        result = redact(text)
        for secret in (
            "abcdefgh12345678",
            "cs_value_123",
            "wJalrXUtnFEMI",
            "uQiRzpo4DXghDmr9QzzfQu27cmVRsG",
            "eyJ1c2VybmFtZSI6ImFkbWluIn0=",
        ):
            assert secret not in result

    def test_json_authorization_header_redacted(self):
        result = redact('{"headers": {"Authorization": "Bearer abcdef0123456789"}}')
        assert "abcdef0123456789" not in result
        result = redact("{'Authorization': 'Basic dXNlcjpwYXNzd29yZA=='}")
        assert "dXNlcjpwYXNzd29yZA==" not in result

    def test_json_x_api_key_header_redacted(self):
        result = redact('{"X-API-Key": "k1"}')
        assert '"k1"' not in result
        assert REDACTED in result

    def test_ordinary_words_unchanged(self):
        for text in (
            "password reset email sent",
            "User bob requested a password reset email",
            "Token refresh scheduled in 5 minutes",
            "secret rotation finished for 3 services",
            "Cookie consent banner shown",
            "signature verification passed",
            "Starting backup to https://backup.example.com/api/v1 with 4 workers",
            "Received GET /users?email=bob@example.com from 10.0.0.5",
        ):
            assert redact(text) == text


class TestRedactorTokenFormats:
    """S-03: standalone token formats that appear in logs without a key name."""

    def test_bare_logshed_api_token_redacted(self):
        token = "ls_live_" + "0123456789abcdef" * 2 + "01234567"
        assert len(token) == 48
        result = redact(f"external client used {token} for /api/v1/logs")
        assert token not in result
        assert result == f"external client used {REDACTED} for /api/v1/logs"

    def test_cookie_header_redacted(self):
        result = redact("Cookie: session=abc123def456; csrftoken=xyz789")
        assert "abc123def456" not in result
        assert "xyz789" not in result
        assert result.startswith("Cookie: ")

    def test_set_cookie_header_redacted(self):
        result = redact("Set-Cookie: logshed_session=s3ss10nvalue; HttpOnly; SameSite=Lax")
        assert "s3ss10nvalue" not in result
        assert result.startswith("Set-Cookie: ")

    def test_json_cookie_header_redacted(self):
        result = redact('{"Cookie": "sid=s3ss10nvalue", "Host": "example.com"}')
        assert "s3ss10nvalue" not in result
        assert '"Host": "example.com"' in result

    def test_discord_webhook_redacted(self):
        for host in ("discord.com", "discordapp.com", "canary.discord.com"):
            url = f"https://{host}/api/webhooks/123456789012345678/AbCdEf_GhIjKl-MnOpQrStUvWxYz0123456789"
            result = redact(f"POST {url} returned 404")
            assert "AbCdEf_GhIjKl-MnOpQrStUvWxYz0123456789" not in result
            assert "123456789012345678" not in result
            assert f"https://{host}/api/webhooks/{REDACTED}" in result

    def test_telegram_bot_token_redacted(self):
        # Fake key; split so secret scanners do not flag it
        token = "123456789:" + "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"
        assert len(token.split(":")[1]) == 35
        result = redact(f"GET https://api.telegram.org/bot{token}/sendMessage failed")
        assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1" not in result
        assert f"https://api.telegram.org/bot{REDACTED}/sendMessage" in result

        result = redact(f"telegram token {token} rejected")
        assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1" not in result

    def test_google_api_key_redacted(self):
        key = "AIzaSyD-1234567890abcdefghijklmnopqrstu"
        assert len(key) == 39
        result = redact(f"GET https://maps.googleapis.com/maps/api/geocode/json?key={key}&address=x")
        assert key not in result
        assert REDACTED in result

    def test_stripe_keys_redacted(self):
        # Fake keys; split so secret scanners do not flag them
        for key in ("sk_" + "live_51H8abcdefGHIJKLmnopQRSTuv", "rk_" + "live_51H8abcdefGHIJKLmnopQRSTuv"):
            result = redact(f"stripe call with {key} failed")
            assert key not in result
            assert result == f"stripe call with {REDACTED} failed"

    def test_gitlab_pat_redacted(self):
        # Fake token; split so secret scanners do not flag it
        token = "glpat" + "-xYz123AbC456dEf789gH"
        result = redact(f"git fetch using {token} denied")
        assert token not in result
        assert result == f"git fetch using {REDACTED} denied"

    def test_github_fine_grained_pat_redacted(self):
        token = "github_pat_11ABCDEFG0123456789abc_" + "x" * 59
        result = redact(f"gh api call with {token}")
        assert token not in result
        assert result == f"gh api call with {REDACTED}"

    def test_github_classic_tokens_redacted(self):
        for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
            token = prefix + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
            result = redact(f"clone failed for {token}")
            assert token not in result

    def test_encrypted_private_key_redacted(self):
        text = (
            "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
            "MIIFHzBJBgkqhkiG9w0BBQ0wPDAbBgkqhkiG9w0BBQwwDgQI\n"
            "-----END ENCRYPTED PRIVATE KEY-----"
        )
        result = redact(f"loaded key:\n{text}\ndone")
        assert "MIIFHzBJBgkqhkiG9w0BBQ0wPDAbBgkqhkiG9w0BBQwwDgQI" not in result
        assert result == f"loaded key:\n{REDACTED}\ndone"

    def test_pgp_private_key_block_redacted(self):
        text = (
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
            "Version: GnuPG v2\n"
            "\n"
            "lQOYBF0abcdEFGHijklMNOPqrstUVWXyz0123456789\n"
            "=a1B2\n"
            "-----END PGP PRIVATE KEY BLOCK-----"
        )
        result = redact(text)
        assert "lQOYBF0abcdEFGHijklMNOPqrstUVWXyz0123456789" not in result
        assert result == REDACTED

    def test_traditional_encrypted_pem_with_headers_redacted(self):
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "Proc-Type: 4,ENCRYPTED\n"
            "DEK-Info: AES-128-CBC,0123456789ABCDEF0123456789ABCDEF\n"
            "\n"
            "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO\n"
            "-----END RSA PRIVATE KEY-----"
        )
        result = redact(text)
        assert "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO" not in result
        assert result == REDACTED

    def test_private_key_without_end_marker_redacted(self):
        text = (
            "key dump: -----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n"
            "AAAAAAABAAABlwAAAAdzc2gtcn"
        )
        result = redact(text)
        assert "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ" not in result
        assert "AAAAAAABAAABlwAAAAdzc2gtcn" not in result
        assert result.startswith("key dump: ")

    def test_certificate_block_unchanged(self):
        text = (
            "-----BEGIN CERTIFICATE-----\n"
            "MIIDdzCCAl+gAwIBAgIEAgAAuTANBgkqhkiG9w0BAQUFADBaMQswCQYDVQQGEwJJ\n"
            "-----END CERTIFICATE-----"
        )
        assert redact(text) == text

    def test_url_signature_parameters_redacted(self):
        text = (
            "GET https://acct.blob.core.windows.net/c/f.txt?sv=2022-11-02&se=2026-01-01&sig=AbCd%2BeFgH%3D&sp=r"
        )
        result = redact(text)
        assert "AbCd%2BeFgH%3D" not in result
        assert f"&sig={REDACTED}&sp=r" in result

        text = "https://bucket.s3.amazonaws.com/k?X-Amz-Credential=x&X-Amz-Signature=0123abcd4567ef"
        result = redact(text)
        assert "0123abcd4567ef" not in result

        text = "callback?user=bob&signature=deadbeefcafe0123"
        result = redact(text)
        assert "deadbeefcafe0123" not in result
        assert "user=bob" in result

    def test_connection_string_scheme_variants_redacted(self):
        for url in (
            "mongodb+srv://admin:Sup3rS3cret@cluster0.example.net/db",
            "rediss://default:Sup3rS3cret@cache.internal:6380",
            "mariadb://root:Sup3rS3cret@10.0.0.4:3306/app",
            "jdbc:postgresql://app:Sup3rS3cret@db:5432/app",
        ):
            result = redact(f"connecting to {url}")
            assert "Sup3rS3cret" not in result
            assert REDACTED in result

    def test_connection_string_keeps_host_and_path(self):
        text = "postgres://app:pw@db.internal:5432/app?sslmode=require"
        assert redact(text) == f"postgres://app:{REDACTED}@db.internal:5432/app?sslmode=require"

    def test_connection_string_user_with_at_sign(self):
        text = "postgres://app@myserver:Sup3rS3cret@myserver.postgres.database.azure.com:5432/db"
        result = redact(text)
        assert "Sup3rS3cret" not in result
        assert "myserver.postgres.database.azure.com:5432/db" in result


# S-04: hostile input must not make redact() quadratic. Lines are kept under the
# per-line truncation limit so these cases exercise the patterns themselves.
def _lines(unit: str, per_line: int, line_count: int = 18) -> str:
    return "\n".join(unit * per_line for _ in range(line_count))


_ADVERSARIAL_INPUTS = {
    "pem_begin_without_end": "-----BEGIN PRIVATE KEY-----\n" * 9500,
    "pem_begin_with_long_bodies": ("-----BEGIN RSA PRIVATE KEY-----" + "A" * 300 + "\n") * 760,
    "pem_begin_single_dashes": "-----BEGIN PRIVATE KEY-----\n" + ("-a" * 5000 + "\n") * 24,
    "url_scheme_repeated_without_at": _lines("http://", 2000),
    "url_userinfo_without_at": _lines("redis://:", 1600),
    "url_at_signs_without_host": _lines("postgres://u:p@", 1000),
    "jwt_prefix_repeated_without_dots": _lines("eyJ-", 3500),
    "jwt_segments_without_third_dot": _lines("eyJabcdefghijklmn.", 800),
    "sshpass_flags_without_p": "sshpass " + "-sshpass\n" * 28000,
    "quoted_secret_without_close": _lines('password="', 1400),
    "escaped_quoted_secret_without_close": _lines('secret=\\"', 1400),
    "sk_token_dash_runs": _lines("sk-" + "-" * 30, 400),
    "curl_user_without_colon": _lines("-u -u ", 2400),
    "telegram_like_digits": _lines("bot123456789:", 1100),
    "cookie_headers": _lines("Cookie: a=b; ", 1100),
}


_WORST_CASE_LIMIT_SECONDS = 1.0


class TestRedactorWorstCase:

    def test_adversarial_inputs_are_large(self):
        for name, text in _ADVERSARIAL_INPUTS.items():
            assert len(text) >= 200_000, name

    @pytest.mark.parametrize("name", sorted(_ADVERSARIAL_INPUTS))
    def test_adversarial_input_redacts_quickly(self, name):
        text = _ADVERSARIAL_INPUTS[name]
        start = time.perf_counter()
        redact(text)
        elapsed = time.perf_counter() - start
        assert elapsed < _WORST_CASE_LIMIT_SECONDS, f"{name} took {elapsed:.2f}s"

    def test_adversarial_list_input_redacts_quickly(self):
        lines = ["http://" * 2000, "eyJ-" * 3500, "-----BEGIN PRIVATE KEY-----" * 500] * 6
        start = time.perf_counter()
        redact(lines)
        assert time.perf_counter() - start < _WORST_CASE_LIMIT_SECONDS

    def test_real_key_still_redacted_among_hostile_markers(self):
        key = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n"
            "-----END PRIVATE KEY-----"
        )
        text = "-----BEGIN PRIVATE KEY-----\n" * 2000 + key + "\npostgres://app:pw0rd@db:5432/app"
        result = redact(text)
        assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7" not in result
        assert "pw0rd" not in result


class TestRedactorTruncation:

    def test_long_single_line_truncated_with_marker(self):
        result = redact("x" * 100_000)
        assert len(result) <= MAX_REDACT_LINE_CHARS + len(TRUNCATION_MARKER)
        assert result.endswith(TRUNCATION_MARKER)

    def test_short_lines_not_truncated(self):
        lines = [f"line {i}: " + "y" * 900 for i in range(60)]
        lines.append("final line password=hunter2")
        text = "\n".join(lines)
        assert len(text) > 3 * MAX_REDACT_LINE_CHARS
        result = redact(text)
        assert TRUNCATION_MARKER not in result
        assert "hunter2" not in result
        assert result.startswith("line 0: ")
        assert result.endswith(f"final line password={REDACTED}")

    def test_only_the_long_line_is_truncated(self):
        text = "first line\n" + "z" * 50_000 + "\nlast line api_key=abcdefgh12345678"
        result = redact(text)
        first, middle, last = result.split("\n")
        assert first == "first line"
        assert middle.endswith(TRUNCATION_MARKER)
        assert len(middle) <= MAX_REDACT_LINE_CHARS + len(TRUNCATION_MARKER)
        assert last == f"last line api_key={REDACTED}"

    def test_list_items_truncated_individually(self):
        result = redact(["short", "w" * 40_000])
        assert result[0] == "short"
        assert result[1].endswith(TRUNCATION_MARKER)

    def test_truncation_does_not_leave_a_token_fragment(self):
        token = "ghp_" + "A1b2C3d4E5" * 3 + "F6g7H8"
        text = "word " * 3270 + token + " tail"
        assert len("word " * 3270) < MAX_REDACT_LINE_CHARS < len("word " * 3270) + len(token)
        result = redact(text)
        assert result.endswith(TRUNCATION_MARKER)
        assert "ghp_" not in result
        assert "A1b2C3d4E5" not in result

    def test_secret_before_cut_still_redacted(self):
        text = "password=hunter2 " + "q" * 30_000
        result = redact(text)
        assert "hunter2" not in result
        assert result.startswith(f"password={REDACTED} ")


class TestRedactorInputCap:
    """N05-3: one call to redact() never processes more than MAX_REDACT_INPUT_CHARS of text."""

    _LINE = "2026-10-19T10:00:00Z web-01 app[123]: user=alice password=hunter2 token=abcdef ok\n"

    def _big_text(self, chars: int) -> str:
        return self._LINE * (chars // len(self._LINE) + 1)

    def test_five_megabyte_input_is_capped_and_fast(self):
        text = self._big_text(5_000_000)
        assert len(text) > 5_000_000
        start = time.perf_counter()
        result = redact(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"redact took {elapsed:.2f}s on a 5 MB input"
        assert result.endswith("\n" + TRUNCATION_MARKER)
        from app.core.redactor import MAX_REDACT_INPUT_CHARS

        # Only the first MAX_REDACT_INPUT_CHARS of input are kept (redaction itself may lengthen them)
        assert result.count("\n") <= MAX_REDACT_INPUT_CHARS // len(self._LINE) + 1
        assert "hunter2" not in result

    def test_cut_falls_on_a_line_boundary(self):
        from app.core.redactor import MAX_REDACT_INPUT_CHARS

        result = redact(self._big_text(MAX_REDACT_INPUT_CHARS + 50_000))
        body = result[: -len("\n" + TRUNCATION_MARKER)]
        assert body.endswith(" ok")
        assert all(line.endswith(" ok") for line in body.split("\n"))

    def test_input_at_the_cap_is_not_cut(self):
        from app.core.redactor import MAX_REDACT_INPUT_CHARS

        text = ("x" * 99 + "\n") * (MAX_REDACT_INPUT_CHARS // 100)
        assert len(text) == MAX_REDACT_INPUT_CHARS
        assert redact(text) == text

    def test_list_items_are_capped_individually(self):
        from app.core.redactor import MAX_REDACT_INPUT_CHARS

        result = redact(["short", self._big_text(MAX_REDACT_INPUT_CHARS + 10)])
        assert result[0] == "short"
        assert result[1].endswith("\n" + TRUNCATION_MARKER)
