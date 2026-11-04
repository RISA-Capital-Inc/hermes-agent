"""Read-time resolution of an ``auxiliary.<task>`` config block.

Built-in slot defaults come from ``DEFAULT_CONFIG``; plugin-registered tasks
(``PluginContext.register_auxiliary_task``) layer their declared ``defaults``
under the user's block and may inherit another slot via ``inherit_from``.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import Any, Dict

logger = logging.getLogger(__name__)

# A gateway session's frozen effective ``auxiliary:`` section (recovered secrets applied, a task whose
# secret changed returned to the primary route). Set for the turn; never the live config.yaml.
_SESSION_AUXILIARY: ContextVar = ContextVar("session_auxiliary", default=None)


@contextlib.contextmanager
def session_auxiliary_scope(auxiliary):
    token = _SESSION_AUXILIARY.set(auxiliary if isinstance(auxiliary, dict) else {})
    try:
        yield
    finally:
        _SESSION_AUXILIARY.reset(token)


class AuthoritativeAuxiliaryTask(str):
    """A task name that keeps the task's non-route policy (timeout, concurrency, extra_body, fallback
    chain) but strips its route fields, so a one-shot fallback naming a complete route never inherits
    an unset endpoint/key/wire mode from the route that just failed (#113322, #130895)."""

    __slots__ = ()
    route_authoritative = True


def authoritative_auxiliary_task(task: str | None) -> str:
    return task if isinstance(task, AuthoritativeAuxiliaryTask) else AuthoritativeAuxiliaryTask(str(task or ""))


def _get_auxiliary_task_config(task: str, _seen: frozenset = frozenset()) -> dict[str, Any]:
    """Config dict for auxiliary.<task>, or {} when unavailable. Plugin-registered tasks get their
    declared defaults layered under user config (user wins); built-in defaults live in DEFAULT_CONFIG.
    A task registered with ``inherit_from`` is resolved here, at read time, over the base task's
    effective config, so it follows the base's current settings (and the active profile's config)
    until the user pins a route on the task itself. ``_seen`` guards re-registration cycles."""
    if not task:
        return {}
    config = _task_config(str(task), _seen)
    if getattr(task, "route_authoritative", False):
        return {key: value for key, value in config.items() if key not in _AUX_ROUTE_KEYS}
    return config


def _task_config(task: str, _seen: frozenset) -> dict[str, Any]:
    aux = _SESSION_AUXILIARY.get()
    if aux is None:
        try:
            from hermes_cli.config import load_config_readonly
            config = load_config_readonly()
        except ImportError:
            return {}
        aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
    task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
    if not isinstance(task_config, dict):
        task_config = {}
    try:
        from hermes_cli.plugins import get_plugin_auxiliary_tasks
        for _entry in get_plugin_auxiliary_tasks():
            if _entry.get("key") == task:
                _defaults = _entry.get("defaults") or {}
                if isinstance(_defaults, dict):
                    _inherit = _entry.get("inherit_from")
                    if _inherit and task in _seen:
                        logger.warning("Auxiliary task %r has a circular inherit_from chain — "
                                       "ignoring inheritance", task)
                    if not _inherit or task in _seen:
                        return {**_defaults, **task_config}
                    base = _get_auxiliary_task_config(_inherit, _seen | {task})
                    return _layer_over_inherited({**base, **_defaults}, task_config)
                break
    except Exception:  # health: allow BLE001 -- plugin discovery must never break aux config reads
        logger.debug("plugin auxiliary task lookup failed for %r", task, exc_info=True)
    return task_config


# The fields that together pick WHERE a call goes. They travel as one unit: a provider pinned on an
# inheriting task must never pick up the base's base_url/api_key (that would send one vendor's key
# to another's endpoint).
_AUX_ROUTE_KEYS = frozenset({"provider", "model", "base_url", "api_key", "api_mode", "key_env",
                             "api_key_env", "reasoning_effort"})


def _layer_over_inherited(inherited: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """Merge a task's own ``auxiliary.<task>`` block over its inherited base.

    The picker, "reset to auto" and the dashboard persist ``provider: auto`` plus ``""`` for
    model/base_url/api_key/reasoning_effort when the operator expresses no preference, so those
    placeholders mean "follow the base", not "override it with nothing". Once the operator pins a
    route (a non-auto provider, a model or a base_url) the whole route comes from the task's own
    block. Non-route keys (timeout, extra_body, ...) override per key; only ``""`` is dropped,
    never other falsy values (``reasoning_effort: false`` is an explicit choice)."""
    provider = str(user.get("provider") or "").strip().lower()
    pinned = (provider not in ("", "auto") or bool(str(user.get("model") or "").strip())
              or bool(str(user.get("base_url") or "").strip()))
    merged = {k: v for k, v in inherited.items() if not (pinned and k in _AUX_ROUTE_KEYS)}
    for key, value in user.items():
        if value == "" and not (pinned and key in _AUX_ROUTE_KEYS):
            continue
        if key == "provider" and not pinned:
            continue
        merged[key] = value
    return merged


# -- Per-model auxiliary routing (model_map) ------------------------------------------------------
#
# ``auxiliary.<task>.model_map`` keys an auxiliary route to the SESSION model instead of to the
# host. A pin written for one model must never survive a switch to another: the compressor sizes
# its trigger from the auxiliary window, so a stale pin silently compacts a long-context session
# hundreds of thousands of tokens early. A model with no entry keeps the block's own behaviour,
# including ``provider: auto`` + empty model, which means "inherit the session model".


def _normalize_model_key(value: Any) -> str:
    return str(value or "").strip().lower()


def aux_model_map_entry(task: str, main_model: str | None,
                        config: dict[str, Any] | None = None) -> dict[str, Any]:
    """``auxiliary.<task>.model_map[main_model]``, or ``{}`` when that model has no entry.

    Matching is case-insensitive and whitespace-tolerant. Keys are matched in file order, so keep
    the map small and unambiguous; an entry that is not a dict is ignored rather than fatal.
    """
    if not task or not _normalize_model_key(main_model):
        return {}
    cfg = _get_auxiliary_task_config(task) if config is None else config
    mapping = cfg.get("model_map") if isinstance(cfg, dict) else None
    if not isinstance(mapping, dict):
        return {}
    wanted = _normalize_model_key(main_model)
    for key, entry in mapping.items():
        if _normalize_model_key(key) == wanted and isinstance(entry, dict):
            return entry
    return {}


def apply_aux_model_map(task: str, main_model: str | None,
                        config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Layer the entry for ``main_model`` over the task's effective config.

    Only the keys the entry sets are overridden; timeouts, ``extra_body`` and fallback chains stay
    as configured. With no entry the config comes back unchanged, so the task keeps following its
    own block - ``provider: auto`` with an empty model inherits the session model and its window.
    """
    base = _get_auxiliary_task_config(task) if config is None else config
    if not isinstance(base, dict):
        base = {}
    entry = aux_model_map_entry(task, main_model, base)
    if not entry:
        return base
    merged = {k: v for k, v in base.items() if k != "model_map"}
    merged.update({k: v for k, v in entry.items() if k != "model_map"})
    return merged


def aux_model_map_context_length(task: str, main_model: str | None,
                                 config: dict[str, Any] | None = None) -> int | None:
    """The ``context_length`` hint declared on that model's entry, or None.

    Local lanes frequently cannot report their own window, so an entry may pin it; the compression
    feasibility probe prefers this hint over re-deriving one from the endpoint.
    """
    raw = aux_model_map_entry(task, main_model, config).get("context_length")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
