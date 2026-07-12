import { defineConfig } from "@hey-api/openapi-ts";

// Types are generated from the running API's OpenAPI schema — the backend's
// Pydantic models are the single source of truth for response shapes.
export default defineConfig({
  input: process.env.OPENAPI_URL ?? "http://localhost:8000/openapi.json",
  output: "src/api/generated",
  plugins: ["@hey-api/typescript"],
});
