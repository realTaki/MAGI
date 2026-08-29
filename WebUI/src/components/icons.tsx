/**
 * Inline SVG icon set.
 *
 * No external icon library — Tailwind v4 + this single file
 * keeps the bundle small and the visual style consistent. All
 * icons share the same 24x24 viewBox, stroke="currentColor" and
 * stroke-width 1.8 so they pick up the surrounding text color
 * (slate-100 in the dark sidebar, slate-400 / sky-700 elsewhere).
 *
 * The default `className` is `h-5 w-5` (20px) which matches what
 * the dashboard's sidebars use. Override per call site for
 * different sizes.
 */

type IconProps = { className?: string };

function Icon({ children, className = "h-5 w-5" }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      className={className}
    >
      {children}
    </svg>
  );
}

// -- chat: 6 EVA-output categories ------------------------------------------

export const IconActionItems = ({ className }: IconProps) => (
  <Icon className={className}>
    <rect x="5" y="4" width="14" height="17" rx="2" />
    <path d="M9 9h6M9 13h6M9 17h4" />
  </Icon>
);

export const IconMeetings = ({ className }: IconProps) => (
  <Icon className={className}>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 9h18M8 3v4M16 3v4" />
  </Icon>
);

export const IconReminders = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M6 8a6 6 0 1 1 12 0c0 4 2 5 2 5H4s2-1 2-5Z" />
    <path d="M10 19a2 2 0 0 0 4 0" />
  </Icon>
);

export const IconEmail = ({ className }: IconProps) => (
  <Icon className={className}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="m3 7 9 6 9-6" />
  </Icon>
);

export const IconScheduledTasks = ({ className }: IconProps) => (
  <Icon className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Icon>
);

export const IconDailyReports = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
    <path d="M15 3v4h4M8 12h8M8 16h6" />
  </Icon>
);

// -- chat: action row icons (新对话 / 搜索对话) -----------------------------

export const IconPlus = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);

export const IconSearch = ({ className }: IconProps) => (
  <Icon className={className}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Icon>
);

// -- knowledge sidebar sections (Skills / Connectors / Contacts) ------------

export const IconSkills = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="m12 3 2.5 5 5.5.8-4 3.9 1 5.5L12 15.6 7 18.2l1-5.5-4-3.9 5.5-.8L12 3Z" />
  </Icon>
);

export const IconConnectors = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M9 7H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h4M15 7h4a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-4" />
    <path d="M9 12h6" />
  </Icon>
);

export const IconContacts = ({ className }: IconProps) => (
  <Icon className={className}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c0-4 4-7 8-7s8 3 8 7" />
  </Icon>
);

// -- MAGI Council sidebar sections (智群管理 / 智能体管理) ------------------

// "MAGI Society" — a tree/council structure. Three connected
// nodes in a hierarchy: the top node is the ADAM, the two
// below are EVAs.
export const IconMagis = ({ className }: IconProps) => (
  <Icon className={className}>
    <circle cx="12" cy="5" r="2.2" />
    <path d="M12 7.2v3" />
    <path d="M12 10.2h-5.5M12 10.2h5.5" />
    <path d="M6.5 10.2v3h5.5M17.5 10.2v3H12" />
    <circle cx="6.5" cy="14.5" r="2.2" />
    <circle cx="17.5" cy="14.5" r="2.2" />
  </Icon>
);

// "MAGI Citizen" — a small chip head with antennae. Reads as
// "MAGI agent" without leaning on the more common robot icon
// (the chip + antennae pair is intentionally retro so the
// sidebar entry doesn't compete visually with the bot-shaped
// TG channel entry elsewhere in the UI).
export const IconMagic = ({ className }: IconProps) => (
  <Icon className={className}>
    <rect x="5" y="7" width="14" height="11" rx="2" />
    <path d="M12 3v4" />
    <circle cx="12" cy="5" r="0.8" fill="currentColor" stroke="none" />
    <circle cx="9" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="15" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <path d="M9 16h6" />
  </Icon>
);

// Brain/neural — used for the Memory pane in the Knowledge tab.
export const IconMemory = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M12 3a5 5 0 0 0-5 5c0 1.5.5 3 1.5 4l-2.9 7.4c-.2.5.2 1.1.7 1.1h11.4c.5 0 .9-.6.7-1.1l-2.9-7.4c1-1 1.5-2.5 1.5-4a5 5 0 0 0-5-5Z" />
    <path d="M9 12h6" />
    <circle cx="10" cy="8" r="0.8" fill="currentColor" stroke="none" />
    <circle cx="14" cy="8" r="0.8" fill="currentColor" stroke="none" />
    <path d="M12 9.5V7" />
  </Icon>
);

// Wrench — used for the Tools pane in the Knowledge tab.
// Slight indented variant: the bolt-and-nut symbol reads
// "settings / capability" without leaning on the more common
// gear icon (which we already have implicit via SidebarShell).
export const IconTools = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-2.4 2.5-2.5z" />
  </Icon>
);

// Lower-case `i` in a circle — used for the InfoTip component.
// Sits next to a card title; hover or focus surfaces a tooltip
// with a longer explanation that doesn't need to live in the
// card body. The dot + stem glyph reads as "info" universally
// (Material/Heroicons/etc. all use this shape), unlike a `?`
// which reads as "question / unknown".
export const IconHelp = ({ className }: IconProps) => (
  <Icon className={className}>
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="8" r="1" fill="currentColor" stroke="none" />
    <path d="M11.25 11h1.5v5.5h-1.5z" fill="currentColor" stroke="none" />
  </Icon>
);

// Pencil — row-level "edit" affordance in data tables.
export const IconEdit = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M16 3l5 5L8 21H3v-5L16 3z" />
  </Icon>
);

// Play triangle — runtime "start" affordance for a stopped agent.
export const IconPlay = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M6 4l14 8-14 8V4z" />
  </Icon>
);

// Solid square — runtime "stop" affordance for a running agent.
export const IconStop = ({ className }: IconProps) => (
  <Icon className={className}>
    <rect x="6" y="6" width="12" height="12" rx="1" />
  </Icon>
);

// Trash — row-level "delete" affordance in data tables.
export const IconDelete = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M3 6h18" />
    <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
    <path d="M10 11v5M14 11v5" />
    <path d="M6 6l1 14a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-14" />
  </Icon>
);

// Checkmark — "save / confirm" affordance.
export const IconCheck = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M5 13l4 4L19 7" />
  </Icon>
);

// X — "close / cancel" affordance.
export const IconX = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M18 6L6 18M6 6l12 12" />
  </Icon>
);

// Eye — "view details" affordance for list rows.
export const IconEye = ({ className }: IconProps) => (
  <Icon className={className}>
    <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z" />
    <circle cx="12" cy="12" r="3" />
  </Icon>
);
