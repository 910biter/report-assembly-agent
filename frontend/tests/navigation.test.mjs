import assert from "node:assert/strict";
import test from "node:test";

import { navigationGroups } from "../src/navigation.ts";

test("sidebar navigation is grouped by workflow and resource", () => {
  assert.deepEqual(
    navigationGroups.map(({ label }) => label),
    ["工作区", "资料中心", "系统"],
  );
  assert.deepEqual(
    navigationGroups.map(({ items }) => items.map(({ label }) => label)),
    [
      ["工作台", "任务"],
      ["材料库", "资料解析", "模板中心"],
      ["系统设置"],
    ],
  );
});
