import assert from "node:assert/strict";
import { test } from "node:test";
import { menuPlacement } from "../dist/core/placement.js";

const bounds = { left: 0, right: 400, top: 0, bottom: 600 };
const rect = (left, top, w = 70, h = 28) => ({ left, right: left + w, top, bottom: top + h });

test("opens below and left-aligned when there is room", () => {
  assert.deepEqual(menuPlacement(rect(20, 100), 200, 150, bounds), { up: false, right: false });
});

test("flips upward when the bottom of the chat is too close", () => {
  assert.deepEqual(menuPlacement(rect(20, 560), 200, 150, bounds), { up: true, right: false });
});

test("aligns right when the menu would run past the right edge (e.g. an Export button in a card header)", () => {
  assert.deepEqual(menuPlacement(rect(320, 100), 200, 150, bounds), { up: false, right: true });
});

test("a Save button on the left never opens off-screen to the left (the reported overlap)", () => {
  // left-aligned button near the bottom of the log: up, but still left-aligned
  const p = menuPlacement(rect(14, 540), 190, 100, bounds);
  assert.equal(p.right, false);
  assert.equal(p.up, true);
});

test("when neither direction fits it prefers the side with more room, and never right-aligns past the left edge", () => {
  const tiny = { left: 0, right: 120, top: 0, bottom: 200 };
  assert.equal(menuPlacement(rect(10, 90), 190, 150, tiny).right, false);
  assert.equal(menuPlacement(rect(10, 120), 190, 150, tiny).up, true);
});
