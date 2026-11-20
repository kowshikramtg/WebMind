/** Icon set (24px grid, stroke-based). Data only, so React and the dependency-free widget render the same shapes. */
export interface IconShape {
  tag: "path" | "circle" | "rect";
  a: Record<string, string>;
}

const p = (d: string): IconShape => ({ tag: "path", a: { d } });

export const ICONS: Record<string, IconShape[]> = {
  chat: [p("M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.6-.7L3 21l1.9-5.1A8.4 8.4 0 1 1 21 11.5z")],
  sparkle: [p("M12 3.5l1.8 4.9 4.9 1.8-4.9 1.8L12 17l-1.8-5-4.9-1.8 4.9-1.8z"), p("M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z")],
  close: [p("M6.5 6.5l11 11M17.5 6.5l-11 11")],
  compose: [p("M12 20h8"), p("M16.5 3.6a2.1 2.1 0 0 1 3 3L7.5 18.6 3.5 19.6l1-4z")],
  send: [p("M12 19V5M5.5 11.5L12 5l6.5 6.5")],
  chevDown: [p("M6 9.5l6 6 6-6")],
  chevRight: [p("M9.5 6l6 6-6 6")],
  download: [p("M12 4v11M7.5 10.5L12 15l4.5-4.5M5 20h14")],
  copy: [p("M9.5 9h9a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z"), p("M5.5 15h-.5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v.5")],
  check: [p("M5 12.5l4.5 4.5L19 7.5")],
  doc: [p("M14 3H7.5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V7.5z"), p("M14 3v4.5h4.5M9 13h6M9 16.5h4")],
  share: [p("M12 15V3.5M8 7.5l4-4 4 4M5 12v6.5A2 2 0 0 0 7 20.5h10a2 2 0 0 0 2-2V12")],
};

export type IconName = keyof typeof ICONS;
