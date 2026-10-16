import asyncio

from fawa2ly.doctor import FAIL, OK, SKIP, WARN, Check, check_config, check_llm, check_offline, check_stt, main


def statuses(checks, area=None):
    return {c.name: c.status for c in checks if area is None or c.area == area}


def test_check_config_reports_key_presence_without_values(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    checks = check_config()
    by_name = {c.name: c for c in checks}
    assert by_name["gemini key"].status == OK
    assert by_name["groq key"].status == SKIP
    assert all("secret-value" not in c.detail for c in checks)
    assert by_name["presets"].status == OK and "swe" in by_name["presets"].detail
    assert by_name["sample CV"].status == OK


def test_check_offline_exercises_replay_and_builder(tmp_path):
    checks = asyncio.run(check_offline(str(tmp_path)))
    assert statuses(checks) == {"STT replay -> detector": OK, "builder -> FakeLLM -> guardrail": OK}
    detector = next(c for c in checks if c.name == "STT replay -> detector")
    assert "question caught at" in detector.detail
    assert not list(tmp_path.iterdir())                    # temporary sample cleaned up


def test_check_stt_skips_without_a_key(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert asyncio.run(check_stt()).status == SKIP


def test_check_llm_skips_without_a_key_and_without_a_default_model(monkeypatch):
    from fawa2ly.llm import VENDORS

    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    assert asyncio.run(check_llm("cerebras")).status == SKIP

    # a vendor whose model must be chosen per key: nothing to probe, so skip rather than fail
    monkeypatch.setitem(VENDORS, "nomodel", {"base_url": "http://x/v1", "key_env": "NOMODEL_KEY",
                                             "model": ""})
    monkeypatch.setenv("NOMODEL_KEY", "k")
    c = asyncio.run(check_llm("nomodel"))
    assert c.status == SKIP and "default model" in c.detail


def test_every_shipped_vendor_has_a_default_model_so_doctor_can_probe_it():
    from fawa2ly.llm import VENDORS
    missing = [name for name, v in VENDORS.items() if not v.get("model")]
    assert missing == [], f"these vendors cannot be health-checked: {missing}"


def test_check_llm_reports_provider_errors(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr("fawa2ly.doctor.OpenAICompatibleLLM.from_env",
                        classmethod(lambda cls, v, *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))))
    c = asyncio.run(check_llm("gemini"))
    assert c.status == FAIL and "boom" in c.detail


def test_check_llm_flags_an_answer_with_no_usable_bullets(monkeypatch):
    from fawa2ly.llm import FakeLLM
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    refusal = FakeLLM(bullets=["I can't help with this. " + " ".join(["word"] * 20)])
    monkeypatch.setattr("fawa2ly.doctor.OpenAICompatibleLLM.from_env",
                        classmethod(lambda cls, v, *a, **k: refusal))
    c = asyncio.run(check_llm("gemini"))
    assert c.status == FAIL and "raw:" in c.detail


def test_check_llm_warns_when_truncated(monkeypatch):
    from fawa2ly.llm import FakeLLM
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr("fawa2ly.doctor.OpenAICompatibleLLM.from_env",
                        classmethod(lambda cls, v, *a, **k: FakeLLM(bullets=["short one"],
                                                                    finish_reason="length")))
    c = asyncio.run(check_llm("gemini"))
    assert c.status == WARN and "TRUNCATED" in c.detail


def test_offline_run_never_touches_the_network(tmp_path, capsys):
    code = main(["--offline", "--tmp-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "stt" not in out.split("\n\n")[0].replace("STT replay", "")   # no live stt row
    assert "ok," in out and "failed" in out


def test_check_line_renders_each_status():
    for status in (OK, FAIL, SKIP, WARN):
        assert Check("llm", "x", status, "d").line().endswith("d")


def test_local_provider_that_is_not_running_is_skipped_not_failed(monkeypatch):
    from fawa2ly.llm import FakeLLM
    monkeypatch.setattr("fawa2ly.doctor.OpenAICompatibleLLM.from_env",
                        classmethod(lambda cls, v, *a, **k: FakeLLM(error="APIConnectionError: refused")))
    assert asyncio.run(check_llm("ollama")).status == SKIP        # local: True
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert asyncio.run(check_llm("gemini")).status == FAIL        # remote: a real failure
