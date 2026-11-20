/**
 * Popover placement. A menu opens below its button and aligned to the button's left edge; if it would run
 * past the chat's right edge it aligns right instead, and if there is not enough room below it opens upward.
 * Both UIs apply the result as the classes `wm-right` / `wm-up`, and the open menu's message is raised above
 * the messages after it (see `.wm-turn:has(.wm-menu)` in the stylesheet), so a menu can never be covered
 * by the next bubble or clipped by the panel.
 */
export interface PlacementRect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export function menuPlacement(
  button: PlacementRect,
  menuWidth: number,
  menuHeight: number,
  bounds: PlacementRect,
  gap = 10,
): { up: boolean; right: boolean } {
  const below = bounds.bottom - button.bottom;
  const above = button.top - bounds.top;
  const up = below < menuHeight + gap && above > below;
  const right = button.left + menuWidth > bounds.right - gap && button.right - menuWidth >= bounds.left + gap;
  return { up, right };
}
