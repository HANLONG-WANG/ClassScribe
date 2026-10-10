// Azure MAI-Transcribe-2 language codes: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe
export const maiLanguages = [
  ["zh", "中文"],
  ["ja", "日本語"],
  ["en", "English"],
  ["yue", "粤语"],
  ["af", "南非语"],
  ["ar", "阿拉伯语"],
  ["as", "阿萨姆语"],
  ["az", "阿塞拜疆语"],
  ["bg", "保加利亚语"],
  ["bn", "孟加拉语"],
  ["bs", "波斯尼亚语"],
  ["ca", "加泰罗尼亚语"],
  ["cs", "捷克语"],
  ["da", "丹麦语"],
  ["de", "德语"],
  ["el", "希腊语"],
  ["es", "西班牙语"],
  ["et", "爱沙尼亚语"],
  ["fa", "波斯语"],
  ["fi", "芬兰语"],
  ["fil", "菲律宾语"],
  ["fr", "法语"],
  ["gl", "加利西亚语"],
  ["gu", "古吉拉特语"],
  ["he", "希伯来语"],
  ["hi", "印地语"],
  ["hu", "匈牙利语"],
  ["hy", "亚美尼亚语"],
  ["id", "印度尼西亚语"],
  ["is", "冰岛语"],
  ["it", "意大利语"],
  ["kk", "哈萨克语"],
  ["kn", "卡纳达语"],
  ["ko", "韩语"],
  ["lt", "立陶宛语"],
  ["lv", "拉脱维亚语"],
  ["mk", "马其顿语"],
  ["ml", "马拉雅拉姆语"],
  ["mr", "马拉地语"],
  ["ms", "马来语"],
  ["nb", "挪威语（书面语）"],
  ["ne", "尼泊尔语"],
  ["nl", "荷兰语"],
  ["or", "奥里亚语"],
  ["pa", "旁遮普语"],
  ["pl", "波兰语"],
  ["pt", "葡萄牙语"],
  ["ro", "罗马尼亚语"],
  ["ru", "俄语"],
  ["sk", "斯洛伐克语"],
  ["sl", "斯洛文尼亚语"],
  ["sv", "瑞典语"],
  ["sw", "斯瓦希里语"],
  ["ta", "泰米尔语"],
  ["te", "泰卢固语"],
  ["th", "泰语"],
  ["tr", "土耳其语"],
  ["uk", "乌克兰语"],
  ["ur", "乌尔都语"],
  ["vi", "越南语"],
] as const;

export type MaiLocale = (typeof maiLanguages)[number][0];
export interface MaiOptions {
  transcribe_style: "verbatim" | "clean";
  timestamps: "word" | "segment" | "none";
  diarization: boolean;
  locale: MaiLocale | null;
  profanity_filter_mode: "None" | "Masked" | "Removed" | "Tags";
  phrases: string[];
  phrase_biasing_weight: number | null;
}
export type MaiDraft = Omit<MaiOptions, "phrases"> & { phraseText: string };
export const defaultMaiDraft: MaiDraft = {
  transcribe_style: "clean",
  timestamps: "word",
  diarization: false,
  locale: "ja",
  profanity_filter_mode: "None",
  phraseText: "",
  phrase_biasing_weight: null,
};

export function maiRequestOptions(draft: MaiDraft): MaiOptions {
  const { phraseText, ...options } = draft;
  return {
    ...options,
    phrases: [
      ...new Set(
        phraseText
          .split("\n")
          .map((phrase) => phrase.trim())
          .filter(Boolean),
      ),
    ],
  };
}

export function maiJobLanguage(locale: MaiLocale | null) {
  return locale === "zh" || locale === "ja" || locale === "en"
    ? locale
    : "auto_mixed";
}
