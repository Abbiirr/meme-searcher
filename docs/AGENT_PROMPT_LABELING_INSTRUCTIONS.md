# Agent Prompt Labeling Instructions

Use this when Codex, Claude Code, OpenCode, or another LLM agent stands in for the human in the feedback loop.

The goal is not to describe the image academically. The goal is to write the kind of natural prompt a user would type when they want this exact meme returned.

Primary labeling source: `data/meme_rlhf`.

For RLHF/evaluation, the agent should inspect each image in `data/meme_rlhf`, generate natural prompts for that image, then test those prompts against the full active corpus. The search system must not be given the target image during retrieval.

The reusable script path is:

```powershell
python -m vidsearch.feedback.target_benchmark build-target-pack --folder data/meme_rlhf --output artifacts/feedback_targets/target_pack.jsonl
python -m vidsearch.feedback.target_benchmark write-target-prompt --pack artifacts/feedback_targets/target_pack.jsonl --output artifacts/feedback_targets/agent_prompt.md --labels-output artifacts/feedback_targets/target_prompts.jsonl
python -m vidsearch.feedback.target_benchmark generate-prompts-gateway --pack artifacts/feedback_targets/target_pack.jsonl --output artifacts/feedback_targets/target_prompts.jsonl --model meme_vlm_captioner --gateway-url %LITELLM_URL% --resume
.\scripts\rlhf_target_benchmark.ps1 -Pack artifacts/feedback_targets/target_pack.jsonl -Prompts artifacts/feedback_targets/target_prompts.jsonl -ReplacePrefix -Train
```

Use `generate-prompts-gateway` for the automated AI-agent path. It calls the LiteLLM gateway at `LITELLM_URL` with `LITELLM_MASTER_KEY`. Direct Ollama generation is an explicit fallback only; if the gateway is down, stop and restore `localhost:4000` rather than silently mixing provider provenance.

## Task

For each meme image:

1. Look at the image as a human would.
2. Identify the memorable hook: quote, joke, person, situation, emotion, or visual scene.
3. Write 5-10 natural search prompts that should retrieve this image.
4. Prefer user-like phrasing over database-like descriptions.
5. Include exact text/OCR only when a real user would likely remember it.
6. Include fuzzy paraphrases for the same intent.

Then run the retrieval test:

1. Search the full corpus with each prompt.
2. Check whether the target image appears in top K.
3. If it appears, select the target image as best.
4. If it does not appear, record a `target_not_found` correction case.
5. Do not select another image merely because it was returned.

## Good Prompt Style

Good prompts sound like this:

```text
find me a meme on I don't have friends, just people I know
show me a meme about knowing lots of people but not having friends
find me that meme about not really having friends
meme about acquaintances not friends
```

Bad prompts sound like this:

```text
two panel image of Heath Ledger in an interview with white subtitle text
image containing the exact OCR: Interviewer: Do you have a lot of friends?
dark low resolution interview screenshot with man wearing suspenders
```

Those details may be useful metadata, but they are usually not how a person searches.

## Prompt Categories

For each image, try to produce prompts in these categories:

- Exact-memory prompt: what the user remembers from the text.
- Paraphrase prompt: same meaning with different words.
- Emotion prompt: the feeling or social situation.
- Topic prompt: the broad theme.
- Named-entity prompt: person, character, show, template, or public figure if obvious.
- Short sloppy prompt: incomplete but realistic user wording.

## Example

Image:

Heath Ledger interview meme. Text says:

```text
Interviewer: Do you have a lot of friends?
Heath Ledger: I don't have a lot of friends. I just know a lot of people.
```

Good labels:

```text
find me a meme on I don't have friends, just people I know
find me a meme about not having friends, only people I know
show me a meme about knowing lots of people but not having friends
find meme for I know many people but I don't have friends
meme on not friends, just people I know
find me that meme about not really having friends
that Heath Ledger meme about friends
meme about acquaintances not friends
find me a lonely friends meme
```

## Applying Feedback

When using these prompts for RLHF:

1. Search with the prompt.
2. If the target image appears, select it as the best result.
3. If the target image does not appear, record a correction case instead of pretending another image was correct.
4. Do not select a result just because it is top-ranked.
5. Do not create synthetic labels that only reward the current retriever.

This creates two datasets:

- Found cases: train/evaluate the learning-to-rank ranker.
- Missing cases: train/evaluate retrieval fixes, such as OCR lexical matching, Unicode normalization, Bangla spelling variants, and query expansion.

## Correction Cases

If a target image is known but absent from search results, create a correction record with:

```json
{
  "prompt": "find me a meme on I don't have friends, just people I know",
  "target_image_path": "data/meme/460488357_842077441453012_1096045244698632582_n.jpg",
  "target_reason": "This is the Heath Ledger friends/acquaintances meme matching the user's wording.",
  "failure_type": "target_not_in_slate"
}
```

These correction records should be handled by a controlled admin/operator path. They should not bypass normal public feedback validation.

## Prompt Label JSONL Schema

The agent writes one row per prompt:

```json
{
  "record_type": "target_prompt_label_v1",
  "target_id": "target-...",
  "prompt_id": "target-...:p1",
  "prompt": "find me a meme on I don't have friends just people I know",
  "category": "exact_memory",
  "operator": "codex-agent",
  "rationale": "The visible meme text is the memorable hook a user would search for."
}
```

`target_id` must come from `artifacts/feedback_targets/target_pack.jsonl`. The prompt text must not include filenames, hashes, image IDs, or local paths.

## Guardrails

- Do not mutate OCR, captions, embeddings, or Qdrant vectors from prompt labels.
- Do not use prompts that no real user would type.
- Do not overfit to filenames.
- Do not mark a label as successful unless the selected image is actually the intended meme.
- Keep multilingual phrasing natural. For Bangla memes, write Bangla prompts the way the user would type them, including common spelling variants.
- Do not confuse ranker failures with retrieval failures. If the target is not in the slate, the ranker cannot fix it.
