import { useTranslation } from "react-i18next";
import { SettingsView } from "@/components/settings/SettingsView";
import { useTheme } from "@/hooks/useTheme";

export default function App() {
  const { t } = useTranslation();
  const { theme, toggle } = useTheme();

  return (
    <div className="flex h-full w-full flex-col bg-background">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border/60 px-4">
        <div className="flex items-center gap-2">
          <img src="/brand/nanobot_icon.png" alt="" className="h-6 w-6 select-none" aria-hidden draggable={false} />
          <span className="text-sm font-medium">{t("app.brand")}</span>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <SettingsView
          theme={theme}
          onToggleTheme={toggle}
          onBackToChat={() => {}}
          onModelNameChange={() => {}}
        />
      </main>
    </div>
  );
}
