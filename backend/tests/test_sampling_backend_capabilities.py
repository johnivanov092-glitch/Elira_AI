from unittest.mock import patch

from app.infrastructure.llm import openai_compatible as provider
from test_openai_compatible_provider import _Response, _llama_env


def test_observed_vllm_uses_native_explicit_sampling_and_does_not_translate_dry(caplog):
    with patch.dict("os.environ", _llama_env()), patch.dict(provider._sampling_backends, {}, clear=True), patch(
        "app.infrastructure.llm.openai_compatible.requests.get",
        side_effect=[_Response({}), _Response({"data": [
            {"id": "local-model", "owned_by": "vllm", "max_model_len": 131072},
        ]})],
    ), patch("app.infrastructure.llm.openai_compatible.requests.post",
             return_value=_Response({"choices": [{"message": {"content": "OK"}}]})) as post:
        provider.server_context_window(fresh=True)
        provider.chat_completion(model="local-model", messages=[{"role": "user", "content": "hi"}],
            options={"sampling": {"dry_multiplier": .8, "repeat_penalty": 1.1, "repetition_penalty": 1.0}})
    body = post.call_args.kwargs["json"]
    assert "dry_multiplier" not in body and "repeat_penalty" not in body
    assert body["repetition_penalty"] == 1.0
    assert "unsupported parameters omitted" in caplog.text


def test_unknown_backend_does_not_claim_dry_support():
    with patch.dict("os.environ", _llama_env()), patch.dict(provider._sampling_backends, {}, clear=True), patch(
        "app.infrastructure.llm.openai_compatible.requests.post",
        return_value=_Response({"choices": [{"message": {"content": "OK"}}]}),
    ) as post:
        provider.chat_completion(model="local-model", messages=[{"role": "user", "content": "hi"}],
                                 options={"sampling": {"dry_multiplier": .8}})
    assert "dry_multiplier" not in post.call_args.kwargs["json"]
