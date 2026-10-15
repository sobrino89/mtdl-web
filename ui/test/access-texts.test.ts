// The sign-in explains what the server refused (mtdl_web/auth.py codes), in the active language.
import assert from "node:assert/strict";
import { test } from "node:test";

import { accessError } from "../src/access-texts";
import { ApiError } from "../src/api";

test("refusals of the sign-in as sentences", () => {
  assert.equal(accessError(new ApiError(403, { code: "password_wrong" })), "Das Passwort stimmt nicht.");
  assert.equal(accessError(new ApiError(429, { code: "too_many_attempts", retry_after: 61 })), "Zu viele Versuche. Bitte in 2 Minuten erneut.");
  assert.equal(accessError(new ApiError(429, { code: "too_many_attempts", retry_after: 5 })), "Zu viele Versuche. Bitte in einer Minute erneut.");
  assert.equal(accessError(new ApiError(422, { code: "password_too_short", min: 8 })), "Mindestens 8 Zeichen.");
  assert.ok(accessError(new ApiError(403, { code: "setup_code_wrong" })).includes("Log des Containers"));
  assert.equal(accessError(new ApiError(500, "boom")), "boom"); // unknown: what the server said
  assert.equal(accessError(new Error("Die beiden Passwörter sind verschieden.")), "Die beiden Passwörter sind verschieden.");
});
