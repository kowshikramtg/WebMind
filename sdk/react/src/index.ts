export { WebMindClient, WebMindError, safeHref } from "./client.js";
export type { WebMindClientOptions } from "./client.js";
export { WebMindProvider, useWebMind } from "./context.js";
export type { ChatTurn, WebMindProviderProps } from "./context.js";
export { WebMindChat, WebMindFloating } from "./WebMindChat.js";
export type { WebMindChatProps, WebMindFloatingProps, WebMindTheme } from "./WebMindChat.js";
export { ChartView, ChartSvg } from "./ChartView.js";
export type { ChartViewProps } from "./ChartView.js";
export { ChartCard, ResultTable, Sources, ExportMenu, Rich, FileCard, MessageActions, Icon } from "./ui.js";
export type { ExportContext, MenuEntry } from "./ui.js";
// Pure helpers (no React): build your own UI or exports on top of them.
export { parseRichText, plainText, safeHttpUrl } from "./core/richtext.js";
export type { Block, Inline } from "./core/richtext.js";
export { menuPlacement } from "./core/placement.js";
export { columnDecimals, computeChartLayout, fmtCell, niceScale, prettyTitle, PALETTE } from "./core/chartgeom.js";
export type { ChartLayout, ChartSpecLike, Mark } from "./core/chartgeom.js";
export {
  answerToMarkdown,
  chartToTable,
  clientExportPolicy,
  downloadBlob,
  safeCell,
  svgToPng,
  svgToStandalone,
  tableToXlsx,
  toCsv,
  toJson,
} from "./core/exports.js";
export type { ClientExportMode } from "./core/exports.js";
export type * from "./types.js";
