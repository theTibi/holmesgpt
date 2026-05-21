"""Tests for optional ClickHouse HTTP JSONEachRow query path."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

sqlalchemy = pytest.importorskip("sqlalchemy")

from holmes.plugins.toolsets.database.database import (  # noqa: E402
    DatabaseConfig,
    _execute_clickhouse_http,
    _parse_clickhouse_http_url,
    _should_use_clickhouse_http_json,
)

pytestmark = getattr(pytest.mark, "db-connectors", pytest.mark)


class TestClickhouseHttpJsonConfig:
    def test_default_disabled(self):
        config = DatabaseConfig(
            connection_url="clickhouse+http://u:p@host:8123/otel"
        )
        assert config.clickhouse_use_http_json is False
        assert _should_use_clickhouse_http_json(config) is False

    def test_enabled_for_clickhouse_url(self):
        config = DatabaseConfig(
            connection_url="clickhouse+http://u:p@host:8123/otel",
            clickhouse_use_http_json=True,
        )
        assert _should_use_clickhouse_http_json(config) is True

    def test_requires_clickhouse_url(self):
        with pytest.raises(ValidationError):
            DatabaseConfig(
                connection_url="postgresql://u:p@host/db",
                clickhouse_use_http_json=True,
            )


class TestParseClickhouseHttpUrl:
    def test_parse_with_auth(self):
        base, db, auth = _parse_clickhouse_http_url(
            "clickhouse+http://user:secret@ch.example:8123/otel"
        )
        assert base == "http://ch.example:8123"
        assert db == "otel"
        assert auth is not None
        assert auth.startswith("Basic ")

    def test_parse_default_port_and_database(self):
        base, db, auth = _parse_clickhouse_http_url(
            "clickhouse+http://localhost/logs"
        )
        assert base == "http://localhost:8123"
        assert db == "logs"
        assert auth is None


class TestExecuteClickhouseHttp:
    @patch("holmes.plugins.toolsets.database.database.urlopen")
    def test_parses_jsoneachrow(self, mock_urlopen):
        body = (
            '{"Timestamp":"2026-03-22T14:26:52.123456789Z","Body":"err"}\n'
            '{"Timestamp":"2026-03-22T14:26:53.000000000Z","Body":"ok"}\n'
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = body.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _execute_clickhouse_http(
            "http://localhost:8123",
            "otel",
            None,
            "SELECT 1",
            effective_limit=10,
        )
        assert result["columns"] == ["Timestamp", "Body"]
        assert result["row_count"] == 2
        assert "123456789" in str(result["rows"][0][0])

    @patch("holmes.plugins.toolsets.database.database.urlopen")
    def test_truncates_at_limit(self, mock_urlopen):
        body = "\n".join(f'{{"id":{i}}}' for i in range(5)) + "\n"
        mock_resp = MagicMock()
        mock_resp.read.return_value = body.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _execute_clickhouse_http(
            "http://localhost:8123",
            "db",
            None,
            "SELECT * FROM t",
            effective_limit=2,
        )
        assert result["row_count"] == 2
        assert result["truncated"] is True


class TestClickhouseSqlalchemyDatetimeBug:
    def test_datetime_converter_fails_on_nanoseconds(self):
        pytest.importorskip("clickhouse_sqlalchemy")
        from clickhouse_sqlalchemy.drivers.http.transport import datetime_converter

        with pytest.raises(ValueError, match="unconverted data remains"):
            datetime_converter("2026-03-22 14:26:52.123456789")

    def test_datetime_converter_ok_microseconds(self):
        pytest.importorskip("clickhouse_sqlalchemy")
        from clickhouse_sqlalchemy.drivers.http.transport import datetime_converter

        dt = datetime_converter("2026-03-22 14:26:52.123456")
        assert dt.year == 2026
