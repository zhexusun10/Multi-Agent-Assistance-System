import assert from "node:assert/strict";
import test from "node:test";
import { createModel } from "../src/model.js";

test("model factory loads installed provider SDKs without a network call", async () => {
  const previousKeys = {
    OPENAI_API_KEY: process.env.OPENAI_API_KEY,
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
    GOOGLE_API_KEY: process.env.GOOGLE_API_KEY,
  };
  process.env.OPENAI_API_KEY = "test-key";
  process.env.ANTHROPIC_API_KEY = "test-key";
  process.env.GOOGLE_API_KEY = "test-key";
  try {
    for (const modelId of [
      "openai:gpt-4.1-mini",
      "anthropic:claude-sonnet-4-6",
      "google-genai:gemini-2.5-flash",
    ]) {
      const model = await createModel(modelId);
      assert.equal(typeof model.invoke, "function");
      assert.equal(typeof model.bindTools, "function");
    }
  } finally {
    for (const [name, value] of Object.entries(previousKeys)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});
