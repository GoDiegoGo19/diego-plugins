# Diego's filmmaking plugins, for a coding agent

Two plugins. Both are plain Python over ffmpeg and small local ONNX models: no plugin system, no API
keys, no network at run time once the weights are in place. A shell agent (Codex, Cursor, Claude Code,
anything that can run commands) uses them straight from a clone.

```bash
git clone https://github.com/GoDiegoGo19/diego-plugins && cd diego-plugins
python3 -m pip install onnxruntime opencv-python-headless numpy tokenizers pillow av scenedetect
# ffmpeg on PATH: brew install ffmpeg (mac) / apt install ffmpeg (linux)
```

- **Watch footage and log it:** `plugins/watch-video/AGENTS.md`
- **Write a Premiere project file:** `plugins/premiere-project-file-assembly/AGENTS.md`

Check either install with its own `verify.sh`, which reports WORKS or names what is missing.

## Rules both plugins expect an agent to respect

1. **A measurement beats a guess.** Values from code over pixels outrank anything a model says. Where
   they disagree the measured value wins and the model's answer is kept beside it as evidence.
2. **An unresolved field stays unresolved.** `unread` and `unknown` mean the machine could not see it.
   Do not fill one in from a file name, a stock listing or a plausible assumption.
3. **Counts never rank anything.** A detector's boxes are for search, never a criterion or a reason.
4. **The models are the workers.** These tools walk their own inputs. Do not fan the work out to
   subagents.
5. **Never overwrite.** Both plugins write a new numbered version every run, by design.

Licence Apache-2.0. Bundled and downloaded models keep their own terms: see `NOTICE`.
