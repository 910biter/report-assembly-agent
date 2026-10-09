import assert from "node:assert/strict";
import test from "node:test";
import { usePersistentPageSize } from "../src/lib/pagination.ts";
import { authMode, currentUser } from "../src/lib/auth.ts";
import { nextTick } from "vue";

test("page size is restored per list and obsolete values fall back", () => {
  const values = new Map();
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };

  const tasks = usePersistentPageSize("tasks");
  assert.equal(tasks.value, 20);
  tasks.value = 5;
  assert.equal(values.get("ira-page-size:local:tasks"), "5");
  assert.equal(usePersistentPageSize("tasks").value, 5);
  assert.equal(usePersistentPageSize("materials").value, 20);

  values.set("ira-page-size:local:analysis", "100");
  assert.equal(usePersistentPageSize("analysis").value, 20);
});

test("page size does not cross authenticated users", async () => {
  const values = new Map();
  const server = new Map([[101, 5], [102, 10]]);
  globalThis.fetch = async (_url, options = {}) => {
    const userId = currentUser.value?.id;
    if (options.method === "PUT") {
      server.set(userId, JSON.parse(options.body).value);
      return { ok: true };
    }
    return { ok: true, json: async () => ({ value: server.get(userId) ?? 20 }) };
  };
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  authMode.value = "on";
  currentUser.value = { id: 101, username: "first", role: "user" };
  const size = usePersistentPageSize("tasks");
  size.value = 5;
  assert.equal(values.get("ira-page-size:101:tasks"), "5");
  currentUser.value = { id: 102, username: "second", role: "user" };
  await nextTick();
  await nextTick();
  assert.equal(size.value, 10);
  size.value = 10;
  assert.equal(values.get("ira-page-size:102:tasks"), "10");
  currentUser.value = { id: 101, username: "first", role: "user" };
  await nextTick();
  await nextTick();
  assert.equal(size.value, 5);
  currentUser.value = null;
  authMode.value = "checking";
});
