# Plan 8: typed decision models for guard, route, grade and critic (research)

**Status: proposal. Nothing in the app depends on it.**

## Idea
Four pipeline steps produce typed decisions, not prose:

| Step | Today | Typed question |
|---|---|---|
| Guard | regular expressions (EN/AR/Franco) | `Noul`: is this a prompt injection / credential request / integrity request? |
| Route | regular expressions over intents | `Choice`: concierge, research, catalog, special collections |
| Relevance grade | fast LLM call (live) | `Noul` per passage: does it help answer the question? |
| Critic | mechanical checks + fast LLM score (live) | `Score`: faithful 1–5; `Noul`: needs a fix |

"System one" decision models answer such questions in one forward pass with calibrated probabilities, instead of generating text. That would cut latency and cost for the grade and critic steps, and give the router a confidence value it could use to ask a clarifying question.

## What exists (checked 2026-10-02)
- [kyegomez/open-jev](https://github.com/kyegomez/open-jev): an unofficial PyTorch reconstruction of the architecture: a bidirectional state encoder, typed Noul/Choice/Score heads and a calibration loss. Its README states it ships **random weights**, so it must be trained before use.
- TypeSafe's Jev itself is closed, API only.
- An "OpenJev" model page exists on Hugging Face (AlquimiaAi/openjev); its contents, licence and quality are unverified.
- We could not find the "Cloudflare Clef" models that were mentioned to us; Cloudflare's own posts list gpt-oss on Workers AI, not a decision model. Treat it as unverified until a model card exists.

## Plan, if pursued
1. **Data.**
   - Labels already exist: golden, dev and held-out (route and mode), red team (guard), and grade/critic verdicts from the AppDB traces once a live pilot runs.
   - Add Arabic and Franco paraphrases.
   - Keep a held-out split untouched.
2. **Model.** A small multilingual encoder (for example BGE-M3 or a ModernBERT-class model) with typed heads, trained with soft-target NLL plus Brier score for calibration.
3. **Gate.**
   - Adopt per step only if, on the held-out split, it matches or beats the current method on accuracy.
   - It must have expected calibration error < 0.05, block all 30 red-team attacks, and stay under 15 ms p95 on CPU.
   - The rules stay as a fallback below a confidence threshold.
4. **Wiring.** A `Decider` interface with the current rules as the default implementation, chosen per step by configuration, with decisions and confidences written to the trace.

## Risks
Guard decisions are security decisions: a learned guard must never replace the rules, only add to them. Training data from real users must stay redacted and inside retention.
