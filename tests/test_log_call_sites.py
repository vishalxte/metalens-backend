"""
Static call-signature check across the whole app package.

WHY THIS EXISTS
───────────────
Reducing the audit_logs schema meant removing helper parameters
(http_status, duration_ms, role, customer_id). Two separate bugs shipped
from that change and both only surfaced at runtime, on the request that
triggered them:

  1. Call sites still passing a removed kwarg:
         AuditService.log_auth() got an unexpected keyword argument 'role'
     -> /auth/login returned 500.

  2. A bulk edit that removed `http_status=200` from log_chat_summary()
     in chat.py — a PRE-EXISTING function that still requires it:
         build_summary_box() missing 1 required keyword-only argument
     -> /chat/ returned 500 after the LLM had already been paid for.

Nothing else catches either one:
  - linters do not check kwargs against a signature;
  - importing the app does not call the functions;
  - unit tests that exercise a service directly with its NEW signature
    pass happily while every real call site is broken.

The failure mode is also worse than it looks. AuditService swallows
exceptions *inside* log(), but a bad keyword fails at the CALL, before
that protection applies — so a logging bug takes down the business
request, which is the one thing the design promises cannot happen.

This test resolves every call in app/ whose target is a project function
and checks it both ways: no unexpected kwargs, no missing required ones.
"""
import ast
import importlib
import inspect
import pathlib

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


def _resolve_imports(tree):
    """
    local name -> imported object, for `from app.x import y`.

    Deliberately keeps NON-callables too. The service singletons
    (`audit_service`, `activity_service`, `cache_service`) are class
    INSTANCES, not functions — filtering to callables here would drop
    them, and with them every `audit_service.log_auth(...)` call site.
    That is exactly the bug this file was written to catch, so the
    filter would have made the test pass while the app was broken.
    """
    resolved = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module or not node.module.startswith("app."):
            continue

        try:
            module = importlib.import_module(node.module)
        except Exception:
            continue

        for alias in node.names:
            obj = getattr(module, alias.name, None)
            if obj is not None:
                resolved[alias.asname or alias.name] = obj

    return resolved


def _target(node, imported):
    """
    The callable a Call node refers to, if we can resolve it.

    Handles both `helper(...)` and `service.method(...)` — the latter is
    how every audit/activity call site is written, and requires the
    imported object itself to be a resolvable instance (see the note in
    _resolve_imports).
    """
    func = node.func

    if isinstance(func, ast.Name):
        obj = imported.get(func.id)
        return obj if callable(obj) else None

    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        base = imported.get(func.value.id)
        if base is not None:
            attr = getattr(base, func.attr, None)
            return attr if callable(attr) else None

    return None


def _check_call(node, fn, path, problems):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return

    name = getattr(fn, "__name__", "?")
    supplied = {kw.arg for kw in node.keywords if kw.arg}
    has_kwargs_splat = any(kw.arg is None for kw in node.keywords)
    accepts_var_kw = any(p.kind == p.VAR_KEYWORD for p in params.values())

    # 1. Unexpected keyword — the /auth/login failure.
    if not accepts_var_kw:
        for extra in sorted(supplied - set(params)):
            problems.append(
                f"{path.name}:{node.lineno} {name}(... {extra}=...) "
                f"— not a parameter"
            )

    # 2. Missing required keyword — the /chat/ failure.
    #    Only checked for all-keyword calls; positional args would make
    #    this ambiguous without full type inference.
    if not has_kwargs_splat and not node.args:
        required = {
            n for n, p in params.items()
            if p.default is p.empty
            and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            and n != "self"
        }
        for missing in sorted(required - supplied):
            problems.append(
                f"{path.name}:{node.lineno} {name}() "
                f"— missing required argument '{missing}'"
            )


def _scan():
    problems, checked = [], 0

    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _resolve_imports(tree)

        if not imported:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            fn = _target(node, imported)
            if fn is None or not callable(fn):
                continue

            checked += 1
            _check_call(node, fn, path, problems)

    return problems, checked


def test_no_call_site_violates_its_signature():
    problems, _ = _scan()

    assert not problems, (
        "Call sites that do not match their function's signature:\n  "
        + "\n  ".join(problems)
    )


def test_the_scanner_is_actually_resolving_calls():
    """
    Guards the guard. If import resolution silently stops working — a
    package rename, a moved module — the test above would pass vacuously
    and stop protecting anything.
    """
    _, checked = _scan()

    assert checked > 50, (
        f"Only {checked} project calls resolved; expected many more. "
        f"The scanner is probably not resolving imports correctly."
    )


def test_logging_services_expose_the_helpers_call_sites_use():
    """
    Renaming a helper without updating callers would otherwise only
    surface at runtime.
    """
    from app.services.audit_service import audit_service
    from app.services.activity_service import activity_service

    expected = {
        audit_service: {
            "log", "log_auth", "log_security", "log_user_event",
            "log_customer_event", "log_document_event",
            "log_knowledge_base_event", "log_ai_event", "log_system_event",
        },
        activity_service: {"track", "track_batch", "track_question"},
    }

    for service, methods in expected.items():
        actual = {m for m in dir(service) if not m.startswith("_")}
        missing = methods - actual
        assert not missing, f"{type(service).__name__} missing: {sorted(missing)}"
