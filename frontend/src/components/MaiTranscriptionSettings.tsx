import { useId } from "react";
import { maiLanguages, type MaiDraft, type MaiLocale } from "../maiOptions";

export function MaiTranscriptionSettings({
  value,
  onChange,
}: {
  value: MaiDraft;
  onChange: (value: MaiDraft) => void;
}) {
  const id = useId();
  return (
    <fieldset className="mai-transcription-settings">
      <legend>Azure MAI 转写选项</legend>
      <div className="form-grid">
        <label>
          <span id={id + "-locale-label"}>MAI 转写语言</span>
          <select
            aria-labelledby={id + "-locale-label"}
            aria-describedby={id + "-locale-help"}
            value={value.locale ?? ""}
            onChange={(event) => {
              onChange({
                ...value,
                locale: (event.target.value || null) as MaiLocale | null,
              });
            }}
          >
            <option value="">自动识别 / 混合语言</option>
            {maiLanguages.map(([code, name]) => (
              <option key={code} value={code}>
                {name}（{code}）
              </option>
            ))}
          </select>
          <small id={id + "-locale-help"}>
            指定一种语言会强制引导识别；混合语言请选择自动。
          </small>
        </label>
        <label>
          <span id={id + "-style-label"}>转写风格</span>
          <select
            aria-labelledby={id + "-style-label"}
            aria-describedby={id + "-style-help"}
            value={value.transcribe_style}
            onChange={(event) => {
              onChange({
                ...value,
                transcribe_style: event.target
                  .value as MaiDraft["transcribe_style"],
              });
            }}
          >
            <option value="verbatim">逐字保留（verbatim）</option>
            <option value="clean">清理口语（clean）</option>
          </select>
          <small id={id + "-style-help"}>
            逐字模式保留语气词和重说；clean 去掉填充词，使文本更易阅读。
          </small>
        </label>
        <label>
          <span id={id + "-timestamps-label"}>时间戳粒度</span>
          <select
            aria-labelledby={id + "-timestamps-label"}
            aria-describedby={id + "-timestamps-help"}
            value={value.timestamps}
            onChange={(event) => {
              onChange({
                ...value,
                timestamps: event.target.value as MaiDraft["timestamps"],
              });
            }}
          >
            <option value="word">按词（word）</option>
            <option value="segment">按段（segment）</option>
            <option value="none">不返回时间戳（none）</option>
          </select>
          <small id={id + "-timestamps-help"}>
            按词可精确定位；按段返回段落时间；无时间戳仅适合文本导出。
          </small>
        </label>
        <label>
          <span id={id + "-profanity-label"}>脏话处理</span>
          <select
            aria-labelledby={id + "-profanity-label"}
            value={value.profanity_filter_mode}
            onChange={(event) => {
              onChange({
                ...value,
                profanity_filter_mode: event.target
                  .value as MaiDraft["profanity_filter_mode"],
              });
            }}
          >
            <option value="Masked">星号遮盖（Masked，服务默认）</option>
            <option value="None">保留原文（None）</option>
            <option value="Removed">删除（Removed）</option>
            <option value="Tags">添加标记（Tags）</option>
          </select>
        </label>
      </div>
      <label className="toggle-line">
        <input
          type="checkbox"
          checked={value.diarization}
          onChange={(event) => {
            onChange({ ...value, diarization: event.target.checked });
          }}
        />
        区分说话人（自动识别）
      </label>
      <div className="form-grid">
        <label>
          <span id={id + "-phrases-label"}>额外术语提示</span>
          <textarea
            aria-labelledby={id + "-phrases-label"}
            aria-describedby={id + "-phrases-help"}
            rows={4}
            value={value.phraseText}
            placeholder={"每行一个词语或短语\n例如：ClassScribe\n神经网络"}
            onChange={(event) => {
              onChange({ ...value, phraseText: event.target.value });
            }}
          />
          <small id={id + "-phrases-help"}>
            与所选课程词典的标准词条合并去重。合并后最多 500 条，每条最多 200
            字符。
          </small>
        </label>
        <label>
          <span id={id + "-weight-label"}>术语提示强度</span>
          <input
            aria-labelledby={id + "-weight-label"}
            aria-describedby={id + "-weight-help"}
            type="number"
            min={0}
            max={2}
            step="any"
            value={value.phrase_biasing_weight ?? ""}
            placeholder="服务默认"
            onChange={(event) => {
              onChange({
                ...value,
                phrase_biasing_weight:
                  event.target.value === "" ? null : event.target.valueAsNumber,
              });
            }}
          />
          <small id={id + "-weight-help"}>
            可设 0–2；留空使用服务默认。对课程词典和额外术语统一生效。
          </small>
        </label>
      </div>
    </fieldset>
  );
}
