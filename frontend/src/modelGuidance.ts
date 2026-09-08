import type { ModelInfo } from "./api";

// Product roles from docs/ClassScribe分阶段开发计划.md §6.1–6.2, §14, §21.
// These descriptions do not select models or claim benchmark superiority.
export const modelGuidance: Record<
  string,
  { purpose: string; limits: string }
> = {
  moss_td_0_9b: {
    purpose:
      "课堂结构分析：识别谁在何时说话，生成说话人、粗时间轴和正文参考候选。",
    limits:
      "结构文本需经正文识别与质检；长录音按窗口处理，不能把官方最长时长视为本机单次保证。",
  },
  firered_asr2_aed: {
    purpose:
      "中文课堂正文、中英混说，以及中文或英语短句复核；提供词级时间戳和置信度。",
    limits: "不支持日语正文；中文初始主候选，实际排名由本机基准决定。",
  },
  firered_asr2_llm: {
    purpose: "中文与中英混说的高精度实验候选。",
    limits: "模型较大，12 GB 显存不作为稳定默认；需单独验证显存与识别质量。",
  },
  firered_vad: {
    purpose:
      "检测音频中的语音区间，为课堂切片和流式语音输入提供说话与停顿边界。",
    limits: "只判断何时有人说话，不识别文字，也不区分说话人身份。",
  },
  firered_lid: {
    purpose:
      "自动／混合语言模式下识别片段语言，帮助路由到中文、日语或英语识别模型。",
    limits: "不生成转录正文；手动指定语言时无需用它判断语言。",
  },
  firered_punc: {
    purpose: "恢复中文和英语正文的标点，补充或修正原生标点结果。",
    limits: "不负责语音识别，不用于日语标点；不应借标点处理自由改写原文。",
  },
  granite_speech_4_1_2b: {
    purpose:
      "日语课堂正文的初始主候选；也用于英语术语强化，支持关键词提示和标点。",
    limits: "不支持中文；不是流式语音输入模型，最终排名需本机实测。",
  },
  qwen3_asr_1_7b: {
    purpose: "中日英统一正文识别、短段复核，以及 IBus 语音输入的平衡模式。",
    limits:
      "建议明确指定语言；流式结果不自带精细时间戳，可交给 ForcedAligner 对齐。",
  },
  qwen3_asr_0_6b: {
    purpose: "侧重低延迟的 IBus 语音输入，也可作为快速识别候选。",
    limits: "较小模型的速度与精度取舍需实测；不保证比大模型更准确。",
  },
  qwen3_forced_aligner_0_6b: {
    purpose:
      "把已经确定的转录文字与音频对齐，生成词或字符时间戳，供字幕和定位使用。",
    limits: "不是正文识别器，不修改文字；仅对质量门控通过的短片段执行。",
  },
  ark_asr_3b: {
    purpose: "中日英高精度正文候选，尤其用于中文和英语的第二模型复核。",
    limits: "非流式；需实测显存和安全片段长度，不依赖它提供主时间轴。",
  },
  moss_transcribe_preview_2b: {
    purpose: "英语课堂正文的初始主候选，也可用于英语语音输入的最高精度确认。",
    limits: "仅用于英语；非低延迟流式模型，需要独立标点和时间对齐处理。",
  },
  nemotron_3_5_asr_streaming_0_6b: {
    purpose: "中日英低延迟流式语音输入，持续生成输入法临时文字。",
    limits: "主要用于 IBus；不承担课堂主时间轴或说话人结构分析。",
  },
  nemotron_speech_streaming_en_0_6b: {
    purpose: "英语 IBus 语音输入的快速／平衡流式候选。",
    limits: "仅用于英语，不适合作为中文或日语识别模型。",
  },
  granite_speech_5_0_turboctc_470m: {
    purpose: "英语超快识别与 CPU／GPU 备用，偏重轻量运行。",
    limits: "不是最高精度默认候选，标点需额外检查。",
  },
  voxtral_mini_4b_realtime_2602: {
    purpose: "实验性中日英统一实时语音识别。",
    limits: "计划中的 BF16 部署需求高于 12 GB；本机仅考虑通过验证的量化实验。",
  },
  vibevoice_asr_streaming_1_5b: {
    purpose: "实验性流式识别与说话人归属，关注实时场景中谁说了什么。",
    limits:
      "默认关闭，名称中的参数量不能直接作为内存估算；需单独验证运行能力。",
  },
  pyannote_community_1: {
    purpose:
      "MOSS 结构失败或异常时的说话人分离回退，标记不同人各自的发言区间。",
    limits: "不识别转录正文；需要满足模型访问条款和安装条件。",
  },
  fun_asr_nano_2512: {
    purpose: "专家测试和热词对照，也作为日语标点效果的实验候选。",
    limits: "默认关闭；有循环输出风险，必须通过本机循环检测及质量测试。",
  },
  whisper_tiny_reference: {
    purpose: "轻量 CPU 参考模型，用于验证真实模型加载、推理和卸载流程。",
    limits: "仅用于工程冒烟测试，不是生产自动最佳候选。",
  },
};
export const capabilityLabels: Record<string, string> = {
  segment_timestamps: "片段时间戳",
  word_timestamps: "词级时间戳",
  confidence: "置信度",
  logprobs: "词元概率",
  punctuation: "标点",
  speakers: "说话人",
  hotwords: "热词／上下文",
};
export const taskLabels: Record<string, string> = {
  confidence: "置信度",
  context: "上下文提示",
  exclusive_diarization: "单一说话人时间轴",
  overlap: "重叠语音",
  speaker_embeddings: "说话人声纹特征",
  truecasing: "大小写恢复",
  asr: "语音转文字",
  streaming: "流式识别",
  timestamps: "时间戳",
  word_timestamps: "词级时间戳",
  diarization: "说话人分离",
  hotwords: "热词",
  events: "声音事件",
  vad: "语音检测",
  lid: "语言识别",
  punctuation: "标点恢复",
  alignment: "文字时间对齐",
};
export const languageLabels: Record<string, string> = {
  zh: "中文",
  ja: "日语",
  en: "英语",
  auto: "自动语言",
  zh_en: "中英混说",
};
export function bodyModel(model: ModelInfo, language: string) {
  return (
    model.enabled &&
    model.tasks.includes("asr") &&
    (!model.modes || model.modes.includes("batch")) &&
    !(
      model.tasks.includes("diarization") && model.tasks.includes("timestamps")
    ) &&
    (model.languages.includes(language) || model.languages.includes("auto"))
  );
}
export function modelReady(model: ModelInfo) {
  return model.install_stage
    ? model.install_stage === "complete"
    : ["healthy", "installed"].includes(model.installation.state);
}
