export type CaptionStyle = 'none' | 'standard' | 'animated';
export type CloudProvider = 'gemini' | 'groq' | 'openai';
export type OpenAIAuthMode = 'api_key' | 'chatgpt';
export interface SubtitleStyle {
  font_family: 'Arial' | 'Impact' | 'Verdana' | 'Inter' | 'Montserrat';
  font_size: number;
  bold: boolean;
  text_color: string;
  highlight_color: string;
  outline_color: string;
  outline_width: number;
  background_color: string;
  background_opacity: number;
  x: number;
  y: number;
  width: number;
}
export interface ChatGPTStatus {
  connected: boolean;
  plan_enabled: boolean;
  pending: boolean;
  error: string | null;
  active_account_id: string | null;
  accounts: { id: string; label: string; email: string | null; active: boolean }[];
  expires_at: string | null;
}
export interface ChatGPTModel {
  id: string;
  name: string;
}
export interface UsageRow {
  provider: CloudProvider;
  auth_mode: OpenAIAuthMode;
  model: string;
  operation: 'review' | 'titles' | 'captions';
  requests: number;
  cache_hits: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens: number | null;
  audio_seconds: number | null;
  estimated_cost_usd: number | null;
  reserved_cost_usd: number;
  unknown_usage_requests: number;
}
export interface UsageSummary {
  day: string;
  request_count: number;
  cache_hits: number;
  estimated_cost_usd: number | null;
  reserved_cost_usd: number;
  unknown_usage_requests: number;
  rows: UsageRow[];
  recent: Record<string, unknown>[];
  pricing: {
    provider: CloudProvider;
    model: string;
    input_per_million: number | null;
    cached_input_per_million: number | null;
    output_per_million: number | null;
    audio_per_hour: number | null;
  }[];
  note: string;
}
export interface IntegrationStatus {
  configured: boolean;
  source: 'environment' | 'local' | 'none';
}
export type Integrations = Record<CloudProvider, IntegrationStatus>;
export interface CropRect {
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface LayoutConfig {
  mode: 'split' | 'blurred';
  facecam: CropRect | null;
  gameplay: CropRect;
  split_ratio: number;
  facecam_fit: 'fit' | 'fill';
  gameplay_fit: 'fit' | 'fill';
  caption_position: 'lower_middle' | 'center' | 'bottom';
}
export interface AIReview {
  provider: CloudProvider;
  model: string;
  score: number;
  rubric: Record<'hook' | 'payoff' | 'reaction' | 'clarity' | 'novelty', number>;
  explanation: string;
  evidence: 'frames_and_transcript' | 'transcript_only';
  created_at: string;
  cache_hit: boolean;
}
export interface Settings {
  max_clips: number;
  sampling_interval: 1 | 2 | 5 | 10;
  analysis_width: 160 | 320 | 480;
  audio_sample_rate: 16000;
  cpu_workers: 1 | 2;
  memory_mode: 'low' | 'balanced';
  clip_duration: number;
  output_resolution: '720x1280' | '1080x1920';
  caption_style: CaptionStyle;
  caption_provider: 'local' | 'groq';
  default_layout: 'split' | 'blurred';
  ai_scoring_provider: 'none' | CloudProvider;
  ai_scoring_model: string;
  openai_auth_mode: OpenAIAuthMode;
  ai_review_mode: 'text' | 'vision';
  auto_ai_review: boolean;
  ai_daily_request_limit: number;
  ai_daily_budget_usd: number;
  ai_max_output_tokens: number;
  ai_allow_unpriced_models: boolean;
  ai_scoring_max_candidates: number;
  ai_scoring_frames: number;
  cloud_consent: boolean;
  ai_refinement: boolean;
  output_folder: string;
  analysis_chunk_seconds: number;
}
export interface AudioStream {
  index: number;
  codec: string;
  sample_rate: number;
  channels: number;
}
export interface MediaMetadata {
  duration: number;
  width: number;
  height: number;
  fps: number;
  video_codec: string;
  audio_codec: string;
  audio_streams: AudioStream[];
  file_size: number;
}
export interface Project {
  id: string;
  name: string;
  source_type: 'local' | 'youtube';
  source_path: string;
  source_url: string | null;
  status: 'ready' | 'importing' | 'analyzing' | 'analyzed' | 'failed';
  created_at: string;
  metadata: Partial<MediaMetadata>;
  highlight_count: number;
  layout?: LayoutConfig | null;
}
export interface TranscriptWord {
  start: number;
  end: number;
  word: string;
}
export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  words: TranscriptWord[];
}
export interface Highlight {
  id: string;
  project_id: string;
  title: string;
  start: number;
  end: number;
  duration: number;
  category: string;
  score: number;
  confidence: number;
  audio_score: number;
  visual_score: number;
  scene_score: number;
  explanation: string;
  selected: boolean;
  transcript: TranscriptSegment[];
  thumbnail_url: string;
  ai_review?: AIReview | null;
  ai_error?: string | null;
  subtitle_style?: SubtitleStyle | null;
  ai_titles?: string[];
  title_error?: string | null;
  caption_status?: 'ready' | 'pending' | 'failed' | 'no_speech';
  caption_error?: string | null;
}
export interface DetectionEvent {
  id: string;
  time: number;
  start: number;
  end: number;
  category: string;
  score: number;
  audio_score: number;
  visual_score: number;
  scene_score: number;
}
export interface EventSnapshot {
  complete: boolean;
  events: DetectionEvent[];
  detail: string;
}
export type JobStatus = 'queued' | 'running' | 'completed' | 'cancelled' | 'failed';
export interface Job {
  id: string;
  project_id: string | null;
  kind: 'analyze' | 'export' | 'youtube' | 'captions' | 'score' | 'titles';
  status: JobStatus;
  stage: string;
  processed_seconds: number;
  total_seconds: number;
  elapsed_seconds: number;
  speed_ratio: number | null;
  eta_seconds: number | null;
  progress: number | null;
  detail: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  result: Record<string, unknown> | null;
}
export interface ExportRecord {
  id: string;
  project_id: string;
  highlight_id: string;
  job_id: string;
  status: string;
  output_path: string;
  filename: string;
  created_at: string;
  error: string | null;
  download_url: string | null;
}
export interface Health {
  status: string;
  version: string;
  capabilities: {
    ffmpeg: boolean;
    ffprobe: boolean;
    yt_dlp: boolean;
    faster_whisper: boolean;
    caption_model_cached: boolean;
    physical_cores: number;
    logical_cores: number;
    memory_gb: number;
  };
  storage: { free_bytes: number; used_bytes: number };
  benchmark: { runs: number; median_speed_ratio: number | null } | null;
}
// Individual benchmark fields are not specified by the shared contract.
export type Benchmark = Record<string, unknown>;
export interface CleanupResult {
  removed_files: number;
  freed_bytes: number;
}
