import { useQuery } from "@tanstack/react-query";
import { api, type ModelInfo } from "../api";
import { bodyModel, languageLabels, modelReady } from "../modelGuidance";

interface Profile {
  language: string;
  scenario: string;
  effective_models?: string[];
  bootstrap_models?: string[];
  models: string[];
}

export function ClassroomModelGuide({
  models,
  language,
  primaryModel,
  strict,
}: {
  models: ModelInfo[];
  language: string;
  primaryModel: string;
  strict: boolean;
}) {
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: () => api<Profile[]>("/profiles"),
  });
  const languages = language === "auto_mixed" ? ["zh", "ja", "en"] : [language];
  const selected = models.find((model) => model.id === primaryModel);
  function describe(model: ModelInfo) {
    return `${model.name}${modelReady(model) ? "（已安装）" : "（尚未就绪）"}`;
  }
  function auxiliary(id: string, name: string) {
    const model = models.find((item) => item.id === id);
    return model ? describe(model) : `${name}（未获取安装状态）`;
  }
  return (
    <section className="mode-model-guide" aria-label="本次处理使用的模型">
      <h2>本次处理使用的模型</h2>
      <p>
        当前版本的快速、平衡、最高精度使用相同的主模型与质量门控复核策略，尚未按这三个档位切换模型。严格单模型只关闭正文的第二、第三模型复核。
      </p>
      {language === "auto_mixed" && (
        <p>
          自动／混合语言会按片段语言选择正文模型；下方分别列出中、日、英的候选。
        </p>
      )}
      {profiles.isError && (
        <p role="alert">无法读取模型候选顺序：{profiles.error.message}</p>
      )}
      <div className="mode-model-routes">
        {languages.map((code) => {
          const profile = (
            Array.isArray(profiles.data) ? profiles.data : []
          ).find(
            (item) => item.scenario === "classroom" && item.language === code,
          );
          const order = primaryModel
            ? (profile?.bootstrap_models ?? profile?.models)
            : (profile?.effective_models ?? profile?.models);
          const candidates = (order ?? []).flatMap((id) => {
            const model = models.find((item) => item.id === id);
            return model && bodyModel(model, code) ? [model] : [];
          });
          const primary = primaryModel
            ? selected
            : strict
              ? undefined
              : (candidates.find(modelReady) ?? candidates[0]);
          const compatible = primary && bodyModel(primary, code);
          const backups = candidates
            .filter((model) => model.id !== primary?.id && modelReady(model))
            .slice(0, 2);
          return (
            <article key={code}>
              <h3>{languageLabels[code]}正文</h3>
              <p>
                <strong>预计主模型：</strong>
                {primary
                  ? describe(primary)
                  : strict
                    ? "请在上方指定主模型"
                    : profiles.isPending
                      ? "正在读取…"
                      : "未获取可用候选"}
              </p>
              {primary && !compatible && (
                <p role="alert">
                  所选主模型不适用于{languageLabels[code]}课堂正文，请更换。
                </p>
              )}
              <p>
                <strong>按需复核：</strong>
                {strict
                  ? "关闭；仅使用指定正文模型"
                  : backups.length
                    ? backups.map(describe).join(" → ")
                    : "当前无已就绪的备用正文模型"}
              </p>
              {!strict && (
                <details>
                  <summary>查看候选优先顺序</summary>
                  <p>
                    {candidates.length
                      ? candidates.map(describe).join(" → ")
                      : "暂无候选信息"}
                  </p>
                </details>
              )}
            </article>
          );
        })}
      </div>
      <p>
        自动最佳优先选择有效排名中已安装的兼容模型；复核只在质量检查触发时执行，最多再调用两个正文模型。上方名单用于预览，开始任务时仍会校验模型文件和运行条件。
      </p>
      <h3>所有模式共用的辅助模型</h3>
      <ul>
        <li>
          语音区间检测：{auxiliary("firered_vad", "FireRedVAD")}
          ；说话人和粗时间轴：{auxiliary("moss_td_0_9b", "MOSS-TD 0.9B")}
          。两者为当前课堂流程必需。
        </li>
        {language === "auto_mixed" && (
          <li>
            自动／混合语言识别：{auxiliary("firered_lid", "FireRedLID")}
            （此语言模式必需）。
          </li>
        )}
        <li>
          结构异常时的说话人回退：
          {auxiliary("pyannote_community_1", "pyannote Community-1")}。
        </li>
        {language !== "ja" && (
          <li>
            中文／英语标点补充：{auxiliary("firered_punc", "FireRedPunc")}
            （已安装且需要时使用）。
          </li>
        )}
        <li>
          最终文字精细对齐：
          {auxiliary("qwen3_forced_aligner_0_6b", "Qwen3-ForcedAligner 0.6B")}
          （通过质量门控时使用；无法精细对齐时保留粗时间）。
        </li>
      </ul>
      {strict && (
        <p>
          “严格单模型”指正文识别不调用备用 ASR；不表示整条流水线只加载一个模型。
        </p>
      )}
    </section>
  );
}
