"""
LLM client — two modes:
1. generate_answer()   : classic RAG answer from cluster snapshot data
2. execute_and_answer(): AI decides which kubectl commands to run, executes them,
                         returns answer with REAL live output (not suggestions).
"""
from groq import Groq
import os, subprocess, json, re

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client

MODEL = "llama-3.3-70b-versatile"

# ── Classic RAG answer (from snapshot data) ──────────────────────────────────
_SYSTEM_RAG = """You are KubeBot — an expert Kubernetes SRE assistant embedded in KubeManager.

Rules:
- Answer ONLY from the CLUSTER DATA provided. Never invent values.
- Be concise: lead with the direct answer, then explain why if useful.
- For issues: state what's wrong → root cause → what was done / recommendation.
- If data is missing say which specific field is absent.
- Format: plain text. Short bullet points only when listing ≥3 items.
- Never say "based on the context" or "according to the data" — just answer.
- Do NOT tell the user to run kubectl commands — they are asking YOU to provide the answer."""

def generate_answer(context: str, query: str) -> str:
    prompt = f"""CLUSTER DATA:\n{context}\n\nQUESTION: {query}"""
    resp = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_RAG},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.0,
        max_tokens=800,
    )
    return resp.choices[0].message.content.strip()


# ── Live command execution mode ───────────────────────────────────────────────
_SYSTEM_PLANNER = """You are KubeBot — an expert Kubernetes AI that runs real commands on a live cluster.

When the user asks a question, decide which kubectl commands to run to get the answer.
You have full cluster-admin access via in-cluster ServiceAccount (no kubeconfig needed).

Respond with a JSON object ONLY — no prose, no markdown fences:
{
  "commands": ["kubectl get pods -A", "kubectl describe pod <name> -n <ns>", ...],
  "reasoning": "brief explanation of why these commands"
}

Rules for commands:
- Use -A or --all-namespaces when the question is cluster-wide
- For pod restart reasons: kubectl get pods -A + kubectl describe pod <pod> -n <ns> (shows restart reason, OOMKilled, exit code, events)
- For logs: kubectl logs <pod> -n <ns> --tail=100 --previous (use --previous if recently crashed)
- For resource usage: kubectl top pods -A and kubectl top nodes (if metrics-server available)
- For events: kubectl get events -n <ns> --sort-by='.lastTimestamp'
- Max 5 commands. Pick the most informative ones.
- For pod restarts use: kubectl delete pod <name> -n <ns>  (safe — the Deployment controller recreates it automatically)
- You may also use: kubectl rollout restart deployment <name> -n <ns>
- Never run: apply, patch, replace, edit, annotate, label, taint, cordon, drain, uncordon
- If you need a specific pod/namespace and don't know it, run kubectl get pods -A first."""

_SYSTEM_SYNTHESIZER = """You are KubeBot — an expert Kubernetes SRE. You have just run live kubectl commands on a Kubernetes cluster and received the real output.

Synthesize the output to directly answer the user's question.

Rules:
- Be direct and specific — use the actual data from the command output
- For restart reasons: check the "Last State", "Exit Code", "Reason" fields in describe output
- For OOMKilled: mention the memory limit and that the container exceeded it
- For CrashLoopBackOff: explain the exit code and check events/logs for root cause
- Highlight important findings (errors, restarts, resource issues)
- Format with clear sections if there's a lot of info
- Do NOT tell the user to run commands — you already ran them and have the answers
- If a command failed (e.g. metrics-server not available), mention that gracefully"""

def _run_safe(cmd: str, timeout: int = 20) -> str:
    """Execute a single kubectl command safely."""
    parts = cmd.strip().split()
    if not parts or parts[0] != "kubectl":
        return f"[skipped non-kubectl command: {cmd}]"
    # Allow safe restart operations explicitly
    if len(parts) >= 3 and parts[1] == "rollout" and parts[2] == "restart":
        pass  # allow rollout restart
    else:
        # Block destructive operations
        danger = {"apply","patch","replace","edit","scale","create",
                   "annotate","label","taint","cordon","drain","uncordon"}
        for part in parts[1:]:
            if part.lower() in danger:
                return f"[blocked: '{part}' is a write operation]"
    try:
        r = subprocess.run(parts, capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        err = r.stderr.strip()
        if not out and err:
            return f"[stderr]: {err}"
        if not out:
            return "(no output)"
        return out
    except subprocess.TimeoutExpired:
        return f"[timed out after {timeout}s]"
    except Exception as e:
        return f"[error: {e}]"


def execute_and_answer(query: str, context_hint: str = "") -> str:
    """
    Full agentic flow:
    1. Ask LLM which commands to run
    2. Execute them live
    3. Synthesize the real output into an answer
    """
    # Step 1: Plan commands
    plan_prompt = f"USER QUESTION: {query}"
    if context_hint:
        plan_prompt += f"\n\nCONTEXT HINT (from indexed data):\n{context_hint}"

    try:
        plan_resp = _get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PLANNER},
                {"role": "user",   "content": plan_prompt},
            ],
            temperature=0.0,
            max_tokens=400,
        )
        plan_raw = plan_resp.choices[0].message.content.strip()
        # Strip any accidental markdown fences
        plan_raw = re.sub(r"```json|```", "", plan_raw).strip()
        plan = json.loads(plan_raw)
        commands = plan.get("commands", [])[:5]
    except Exception as e:
        # Fallback: run a safe default
        commands = ["kubectl get pods -A", "kubectl get events -A --sort-by='.lastTimestamp'"]

    if not commands:
        commands = ["kubectl get pods -A"]

    # Step 2: Execute
    results = []
    for cmd in commands:
        output = _run_safe(cmd)
        results.append(f"$ {cmd}\n{output}")

    combined_output = "\n\n".join(results)

    # Step 3: Synthesize
    synth_prompt = f"""USER QUESTION: {query}

LIVE COMMAND OUTPUT:
{combined_output}"""

    synth_resp = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_SYNTHESIZER},
            {"role": "user",   "content": synth_prompt},
        ],
        temperature=0.0,
        max_tokens=900,
    )
    answer = synth_resp.choices[0].message.content.strip()

    # Append raw command outputs as collapsible reference
    answer += f"\n\n─────────────────────────────\nCommands executed:\n"
    for cmd in commands:
        answer += f"  • {cmd}\n"

    return answer
