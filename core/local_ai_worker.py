"""One review per process, input/output via pipes, no files containing case text."""
import json
import os
import sys


def main():
    model = None
    try:
        raw = sys.stdin.buffer.read(160000 + 1)
        if len(raw) > 160000:
            return 1
        payload = json.loads(raw)
        from llama_cpp import Llama
        model = Llama(model_path=os.environ["DOCKET_LOCAL_MODEL_PATH"].strip(), n_ctx=8192,
                      n_gpu_layers=0, verbose=False, seed=0)
        # Check headroom before applying the model's chat template. Oversized inputs
        # fail closed; the model may also reject templates beyond its context window.
        tokens = sum(len(model.tokenize(message["content"].encode("utf-8"))) for message in payload["messages"])
        if tokens > 6000:
            return 1
        result = model.create_chat_completion(messages=payload["messages"],
            response_format={"type": "json_object", "schema": payload["schema"]},
            temperature=0, max_tokens=1000)
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            return 1
        output = choice["message"]["content"].encode("utf-8")
        if len(output) > 32000:
            return 1
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Parent reports a sanitized failure and keeps the original source/rule result.
        return 1
    finally:
        if model:
            model.close()


if __name__ == "__main__":
    raise SystemExit(main())
