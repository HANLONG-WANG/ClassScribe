import type { Manuscript } from "./manuscripts";

export interface ManuscriptGroup {
  key: string;
  label: string;
  older: boolean;
  items: Manuscript[];
}

function calendarDay(date: Date) {
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
}

export function groupManuscripts(
  items: Manuscript[],
  now = new Date(),
): ManuscriptGroup[] {
  const today = calendarDay(now);
  const groups = new Map<number, ManuscriptGroup>();
  for (const item of items) {
    const date = new Date(item.created_at);
    const day = Number.isNaN(date.getTime()) ? -Infinity : calendarDay(date);
    let group = groups.get(day);
    if (!group) {
      const daysAgo = (today - day) / 86_400_000;
      const label =
        day === -Infinity
          ? "日期未知"
          : daysAgo === 0
            ? "今天"
            : daysAgo === 1
              ? "昨天"
              : date.toLocaleDateString("zh-CN", {
                  ...(daysAgo >= 7 || date.getFullYear() !== now.getFullYear()
                    ? { year: "numeric" as const }
                    : {}),
                  month: "long",
                  day: "numeric",
                  weekday: "short",
                });
      group = { key: String(day), label, older: daysAgo >= 7, items: [] };
      groups.set(day, group);
    }
    group.items.push(item);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => right - left)
    .map(([, group]) => group);
}
