/**
 * 设计令牌（Codex 风格）—— 近黑中性灰阶 + 等宽字体承担机械感
 *
 * 和上一版（PostHog 珊瑚橙 + 圆角卡片）的区别：
 * - 颜色：从"深藏紫蓝 + 珊瑚橙"换成中性近黑；彩色只留给状态（绿/红/黄/蓝）
 * - 形状：圆角 10~12px → 6px，去掉阴影，靠 1px 描边分层
 * - 字体：等宽字体出现在"系统说的话"上（会话 id / 工具名 / 耗时 / token / 体积）
 *
 * 样式分两处：结构性样式在 index.css（配合同名 CSS 变量），
 * 组件里的内联样式只留"随状态变化"的那几处 —— 上一版几百行 style 对象是主要维护成本。
 */
import { theme } from 'antd';

/** 与 index.css 的 :root 变量一一对应：改颜色只改这两处 */
export const T = {
  bg: '#0a0a0a',
  panel: '#0f0f0f',
  elev: '#151515',
  hover: '#1b1b1b',
  border: '#232323',
  borderSoft: '#1a1a1a',
  text: '#ededed',
  text2: '#a1a1a1',
  text3: '#6e6e6e',
  primary: '#ffffff',
  primaryText: '#0a0a0a',
  ok: '#3fb950',
  err: '#f85149',
  warn: '#d29922',
  info: '#58a6ff',
  mono: "ui-monospace, SFMono-Regular, Menlo, Consolas, 'JetBrains Mono', monospace",
} as const;

/** antd 组件（看板 / 登录 / 弹窗 / 消息提示仍在用）对齐同一套令牌，避免两种视觉打架 */
export const codexTheme = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: T.primary,
    colorInfo: T.info,
    colorSuccess: T.ok,
    colorError: T.err,
    colorWarning: T.warn,
    colorLink: T.info,
    colorBgBase: T.bg,
    colorBgContainer: T.panel,
    colorBgElevated: T.elev,
    colorBorder: T.border,
    colorBorderSecondary: T.borderSoft,
    colorTextBase: T.text,
    colorText: T.text,
    colorTextSecondary: T.text2,
    colorTextTertiary: T.text3,
    borderRadius: 6,
    fontSize: 13,
    lineHeight: 1.65,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
    controlHeight: 30,
  },
  components: {
    Button: { primaryColor: T.primaryText, fontWeight: 500, defaultBg: T.elev, defaultBorderColor: T.border },
    Card: { headerBg: 'transparent', borderRadiusLG: 6 },
    Modal: { contentBg: T.elev, headerBg: T.elev },
    Menu: { itemBg: 'transparent', itemSelectedBg: T.hover, itemHeight: 32 },
    Table: { headerBg: T.elev, borderColor: T.border },
  },
};

/** 意图 → 标签与状态点颜色（supervisor 的分类结果，只有点用彩色） */
export const INTENT_LABEL: Record<string, string> = {
  analysis: '数据分析',
  content: '内容生成',
  service: '客服问答',
  document: '文档处理',
};
export const INTENT_DOT: Record<string, string> = {
  analysis: T.info,
  content: T.warn,
  service: T.ok,
  document: T.text3,
};
