# Galileo Troubleshooter (public runbooks)

Symptom-first failure-mode runbooks for Galileo — flattened into a single static HTML page (Karpathy [rendergit](https://github.com/karpathy/rendergit)-style) for browsing, Ctrl+F, and LLM paste.

**Open:** [index.html](./index.html)  
**Local:** open this folder via any static server, or on GitHub Pages at `/troubleshooter/`.

## What you get

- 26 copy-pasteable runbooks (auth, logging, metrics, experiments, OTel, Protect drills, …)
- 5 reference pages (failure-mode backlog, common errors, troubleshooting, MCP setup, comparisons)
- Human view + LLM/CXML view
- Internal planning / private ops paths omitted from this public flatten

## Why it belongs in this repo

Trinity Stack shows **six cross-layer failures with live Console evidence**.  
These runbooks are the **portable fix catalog** behind that practice: when a customer hits a LiteLLM 403, missing ground truth, or uncorrelated OTel spans, they get a symptom → check → fix path without needing the full lab harness.

Together: **demo that proves the thesis** + **runbooks that unblock the next engineer**.
