# RTFI - Real-Time Instruction Compliance Risk Scoring

**Closing the LLM Instruction Gap with deterministic risk scoring. Because managing AI risk is tractable even when fixing AI behavior isn't.**

[RTFI](https://github.com/lcatlett/rtfi) is a Claude Code plugin and deterministic engine that predicts when AI sessions are at risk of ignoring your instructions, enabling proactive intervention before failures occur.

## **⚖️ Project Philosophy**

**Predicting the exact millisecond your LLM stops being an assistant and starts being a liability.**

Most AI tooling tries to "fix" AI behavior—an exercise in futility when dealing with non-deterministic black boxes. **RTFI** operates on a different premise: **Fixing AI behavior is impossible, but managing AI risk is tractable.**

Instead of chasing hallucinations after they happen, we use a deterministic engine to monitor the structural conditions that lead to failure:

* **Vibes are not a strategy:** We don't use an LLM to monitor your LLM. That's just recursive chaos. We use math.  
* **The "Agent Apocalypse" constant:** Parallel agents don't scale productivity; they scale entropy. We quantify [Agent Fanout](https://github.com/lcatlett/rtfi/blob/main/docs/ARCHITECTURE.md) before it burns your budget.  
* **Context is a liability:** The longer the chat, the lower the compliance. We treat context length as a countdown, not a feature.

## **🚩 The Problem: The Instruction Gap**

LLMs in production workflows ignore explicit instructions at unpredictable rates. You write a system prompt or a CLAUDE.md file, the AI acknowledges it, and then it proceeds to disregard your constraints without notification.

This is a documented systemic failure known as the **Instruction Gap**. Research confirms that even the most "advanced" models suffer from structural non-compliance:

* [**MOSAIC Benchmark**](https://github.com/lcatlett/rtfi/blob/main/docs/PRODUCT-BRIEF.md)**:** Proves that models suffer from "lost in the middle" biases; instructions buried in the context are ignored significantly more often.  
* [**InFoBench**](https://github.com/lcatlett/rtfi/blob/main/docs/PRODUCT-BRIEF.md)**:** Shows that even large-scale instruction-tuned LLMs fail to follow simple constraints in zero-shot settings.  
* **The Compliance Reality:** In enterprise scenarios, leading LLMs have been found to rack up **660 to 1,330 instruction violations** in a single session.

**RTFI** closes this gap by shifting the focus from reactive "hallucination detection" to proactive **deterministic risk scoring.**

## **🧮 How It Works: The Scoring Engine**

RTFI calculates a total **Compliance Risk Score** (![][image1]) using a weighted sum of five deterministic signals. Unlike "evals" that look at output, RTFI looks at the *environment* of the session.

### **The Formula**

![][image2]Where:

* ![][image3] is the normalized score (![][image4]) for each factor.  
* ![][image5] is the assigned weight for that factor.

### **Risk Factor Breakdown**

|

| **Factor** | **Variable** | **Weight (W)** | **Logic** |

| **Agent Fanout** | ![][image6] | ![][image7] | ![][image8] |

| **Context Length** | ![][image9] | ![][image10] | ![][image11] |

| **Autonomy Depth** | ![][image12] | ![][image10] | ![][image13] |

| **Decision Velocity** | ![][image14] | ![][image15] | ![][image16] |

| **Instruction Displacement** | D | 0.10 | min(1.0, skill_tokens_injected / instruction_tokens) |

When ![][image17], RTFI triggers a high-risk alert. At this point, the probability of instruction drift is high enough that human intervention is required to "re-center" the agent.

**Instruction Displacement** measures how much ambient context (injected skill prompts, sub-agent output, tool results) has crowded out your CLAUDE.md. When the ratio approaches 1.0, your standing instructions are structurally at risk of being ignored — regardless of what the model "intends" to do.

## **🧷 Compliance Artifact Tracker (Behavioral Layer)**

Risk scoring tells you when a session is *likely* drifting. The Compliance Artifact Tracker tells you when it *actually* did.

Displacement is a leading indicator (skill prompts injected > CLAUDE.md size). Compliance is a lagging indicator: **did Claude write the files its standing instructions required?** Because enforcement lives in hooks — not CLAUDE.md — it can't itself be displaced out of context.

**How it works:**

1. Configure the files you expect every session to touch via `RTFI_EXPECTED_ARTIFACTS` (CSV, default: `CONTEXT.md`).
2. RTFI observes every `Write` / `Edit` tool call in `PostToolUse`, path-resolved against `$CLAUDE_PROJECT_DIR` (paths outside the project are ignored).
3. At `Stop`, RTFI diffs expected vs. observed. Missing artifacts are persisted as `compliance_failures`, the session row is flagged `compliance_violated=1`, and a `COMPLIANCE_CHECK` entry is written to the HMAC-signed audit log.

**Where you see the result:**

* **Dashboard:** new **Compliance** column — ✓ (PASS), ✗ (FAIL, tooltip lists missing artifacts), or **N/A** when no artifacts are configured.
* **`/api/compliance-stats`:** aggregate showing `high_displacement_violated / high_displacement_total` — answers "when displacement was high, how often did Claude actually stop following instructions?"
* **`/rtfi:check`:** the per-session report now includes an Artifact Compliance line with `expected`, `observed`, and `missing` arrays (in both text and JSON output).

**Opt-out:** set `RTFI_EXPECTED_ARTIFACTS=""` to disable enforcement; the dashboard renders `N/A` and no scoring behavior changes.

## **🚀 Quick Start**

### **Installation**

\# Clone the repository  
git clone \[https://github.com/lcatlett/rtfi.git\](https://github.com/lcatlett/rtfi.git)  
cd rtfi

\# Run setup  
bash scripts/setup.sh

### **First-Run Setup**

Run the setup wizard to validate your environment and create the default config:

python3 scripts/rtfi\_cli.py setup

## **🕹️ Usage & Commands**

| **Command** | **Description** |

| /rtfi:dashboard | **Launch the live web dashboard** with real-time risk gauges. |

| /rtfi:checkpoint | Reset autonomy depth (tell RTFI "I'm back in control"). |

| /rtfi:risky | Show sessions that exceeded your risk threshold. |

| /rtfi:check | Validate a session against declared constraints and report artifact compliance (expected/observed/missing). |

## **📊 Web Dashboard**

RTFI includes a live dashboard for monitoring. It shows:

* **Live Risk Gauge:** A color-coded (Green/Amber/Red) ring indicator that updates every 2 seconds.  
* **Factor Breakdown:** Real-time bars showing exactly which metric is pushing you toward a liability.  
* **Session History + Compliance Column:** Every past session with its risk score and a ✓ / ✗ / N/A badge indicating whether expected artifacts were actually written.  
* **Audit Trail:** Tamper-evident logs of every tool call and its associated risk at that moment.

To start: python3 scripts/rtfi\_dashboard.py

## **📂 Documentation**

* [**Architecture**](https://github.com/lcatlett/rtfi/blob/main/docs/ARCHITECTURE.md)**:** Deep dive into the deterministic engine and scoring logic.  
* [**Product Brief**](https://github.com/lcatlett/rtfi/blob/main/docs/PRODUCT-BRIEF.md)**:** Market analysis, the "Instruction Gap," and academic benchmarks.  
* [**Troubleshooting**](https://github.com/lcatlett/rtfi/blob/main/docs/TROUBLESHOOTING.md)**:** Common issues and health checks.

## **📄 License**
MIT
