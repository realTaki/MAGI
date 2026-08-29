/**
 * KnowledgeTab — Skills / Connectors / Contacts / Memory / Tools.
 *
 * Each pane lives in its own file under knowledge/.
 */
import { useState } from "react";

import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconConnectors,
  IconContacts,
  IconMemory,
  IconSkills,
  IconTools,
} from "../components/icons";
import { useT } from "../i18n/index";
import { KnowledgeConnectorsPane } from "./knowledge/KnowledgeConnectorsPane";
import { KnowledgeContactsPane } from "./knowledge/KnowledgeContactsPane";
import { KnowledgeMemoryPane } from "./knowledge/KnowledgeMemoryPane";
import { KnowledgeSkillsPane } from "./knowledge/KnowledgeSkillsPane";
import { KnowledgeToolsPane } from "./knowledge/KnowledgeToolsPane";
import { KnowledgeMCPPane } from "./knowledge/KnowledgeMCPPane";

type KnowledgeSection = "skills" | "connectors" | "contacts" | "memory" | "tools" | "mcp";

const KNOWLEDGE_SECTIONS: SidebarItem[] = [
  { id: "skills", label: "sidebar.knowledgeSkills", icon: <IconSkills /> },
  { id: "connectors", label: "sidebar.knowledgeConnectors", icon: <IconConnectors /> },
  { id: "contacts", label: "sidebar.knowledgeContacts", icon: <IconContacts /> },
  { id: "memory", label: "sidebar.knowledgeMemory", icon: <IconMemory /> },
  { id: "tools", label: "sidebar.knowledgeTools", icon: <IconTools /> },
  { id: "mcp", label: "sidebar.knowledgeMcp", icon: <IconTools /> },
];

export default function KnowledgeTab() {
  const t = useT();
  const [section, setSection] = useState<KnowledgeSection>("skills");
  return (
    <SidebarShell
      items={KNOWLEDGE_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as KnowledgeSection)}
      ariaLabel={t("sidebar.knowledgeNavAria")}
    >
      {section === "skills" && <KnowledgeSkillsPane />}
      {section === "connectors" && <KnowledgeConnectorsPane />}
      {section === "contacts" && <KnowledgeContactsPane />}
      {section === "memory" && <KnowledgeMemoryPane />}
      {section === "tools" && <KnowledgeToolsPane />}
      {section === "mcp" && <KnowledgeMCPPane />}
    </SidebarShell>
  );
}
