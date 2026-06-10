# Optional Hermes Core Patch Draft: Mandatory Final Gate

LifeEngine v0.99 works as a normal Hermes plugin using `pre_llm_call`
and `transform_llm_output`.  Current Hermes plugin hooks isolate callback
exceptions so one plugin cannot break the agent loop.  That is correct for normal
plugins, but a strict life-state kernel may want a fail-closed final gate.

This draft proposes a tiny host-level seam.  It is **not** required for the
embedded plugin to run.

## Desired contract

- Register hook name: `mandatory_final_gate`.
- Called after normal `transform_llm_output`, before the final response is
  persisted or delivered.
- Return shapes:
  - `{"action":"allow"}`
  - `{"action":"replace", "response_text":"..."}`
  - `{"action":"block", "response_text":"...", "reason":"..."}`
- If a mandatory gate callback raises, host returns a safe block response instead
  of silently delivering the original response.

## Sketch

```diff
diff --git a/hermes_cli/plugins.py b/hermes_cli/plugins.py
@@
 VALID_HOOKS = {
@@
     "transform_llm_output",
+    "mandatory_final_gate",
@@
 }
+
+def invoke_mandatory_hook(hook_name: str, **kwargs):
+    callbacks = _plugin_manager._hooks.get(hook_name, [])
+    results = []
+    for cb in callbacks:
+        try:
+            ret = cb(**kwargs)
+            if ret is not None:
+                results.append(ret)
+        except Exception as exc:
+            return [{
+                "action": "block",
+                "response_text": "LifeEngine final gate failed closed before delivery.",
+                "reason": f"{type(exc).__name__}: {exc}",
+            }]
+    return results
```

```diff
diff --git a/agent/conversation_loop.py b/agent/conversation_loop.py
@@
 # after transform_llm_output and before final delivery/persistence
+try:
+    from hermes_cli.plugins import invoke_mandatory_hook
+    for gate_result in invoke_mandatory_hook(
+        "mandatory_final_gate",
+        response_text=final_response,
+        session_id=agent.session_id,
+        task_id=effective_task_id,
+        turn_id=turn_id,
+        model=agent.model,
+        platform=getattr(agent, "platform", None) or "",
+    ):
+        if isinstance(gate_result, dict):
+            action = gate_result.get("action")
+            if action in {"replace", "block"} and gate_result.get("response_text"):
+                final_response = str(gate_result["response_text"])
+                break
+except Exception as exc:
+    final_response = "LifeEngine mandatory final gate failed closed before delivery."
```

## LifeEngine plugin behavior if this hook exists

LifeEngine can register the same audit callback for both:

- `transform_llm_output` for current Hermes compatibility.
- `mandatory_final_gate` for fail-closed hosts.
