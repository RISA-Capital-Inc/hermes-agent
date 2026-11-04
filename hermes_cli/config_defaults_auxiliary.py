"""Pure-data factory for auxiliary model configuration defaults."""


def _aux(timeout, *, reasoning_effort=True, **extra):
    """Standard auxiliary-task model block (see DEFAULT_CONFIG["auxiliary"]).

    reasoning_effort=False omits that key (MoA blocks configure depth per slot);
    ``extra`` keys are appended after the standard ones.
    """
    d = {"provider": "auto", "model": "", "base_url": "", "api_key": "", "timeout": timeout, "extra_body": {},
         # Per-session-model routing: a key here wins over the block above for that session model,
         # and models without a key keep provider "auto" (inherit the session model). Declared on
         # every standard block so `hermes config get`/`set` recognize the key instead of warning.
         "model_map": {}}
    if reasoning_effort:
        d["reasoning_effort"] = ""
    d.update(extra)
    return d
