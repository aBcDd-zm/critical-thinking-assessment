import { api } from "./client";
import type {
  AssessmentReportResponse,
  CreateSessionPayload,
  ContinueStageResponse,
  DialogueTurnItem,
  FinishSessionResponse,
  FeedbackResponse,
  FeedbackStateResponse,
  SessionResponse,
  LanguageModeResponse,
  PreparationResponse,
  SkipStageResponse,
  SubmitFeedbackPayload,
  SubmitTurnPayload,
  SubmitTurnResponse,
} from "../types/session";

export interface SubmitTurnStreamEvent {
  event:
    | "user_turn_saved"
    | "agent_started"
    | "agent_heartbeat"
    | "agent_delta"
    | "agent_completed"
    | "error";
  message?: string;
  detail?: string;
  delta?: string;
  elapsed_seconds?: number;
  session_uuid?: string;
  saved_turn_index?: number;
  next_action?: string;
  duration_ms?: number;
  ai_turn?: DialogueTurnItem | null;
  replayed?: boolean;
}

export interface ProfileTurnStreamEvent {
  event:
    | "profile_answer_saved"
    | "profile_agent_started"
    | "profile_agent_completed"
    | "profile_completed"
    | "error";
  message?: string;
  detail?: string;
  session_uuid?: string;
  saved_turn_index?: number;
  ai_turn?: DialogueTurnItem | null;
}

export async function createAssessmentSession(payload: CreateSessionPayload) {
  const response = await api.post<SessionResponse>("/sessions", {
    info_collect_method: "ai_dialogue",
    ...payload,
  });
  return response.data;
}

export async function getAssessmentSession(sessionUuid: string) {
  const response = await api.get<SessionResponse>(`/sessions/${sessionUuid}`);
  return response.data;
}

export async function getAssessmentTurnSpeech(
  sessionUuid: string,
  turnIndex: number,
  signal?: AbortSignal,
) {
  const response = await api.get<Blob>(
    `/sessions/${encodeURIComponent(sessionUuid)}/turns/${turnIndex}/speech`,
    {
      responseType: "blob",
      signal,
    },
  );
  return response.data;
}

export interface SpeechTranscriptionResponse {
  text: string;
  provider: string;
  request_id?: string;
}

export async function transcribeAssessmentSpeech(
  sessionUuid: string,
  audio: Blob,
  signal?: AbortSignal,
) {
  const response = await api.post<SpeechTranscriptionResponse>(
    `/sessions/${encodeURIComponent(sessionUuid)}/speech/transcriptions`,
    audio,
    {
      headers: {
        "Content-Type": audio.type || "application/octet-stream",
      },
      signal,
      timeout: 30_000,
    },
  );
  return response.data;
}

export async function getAssessmentPreparation(sessionUuid: string) {
  const response = await api.get<PreparationResponse>(
    `/sessions/${sessionUuid}/preparation`,
  );
  return response.data;
}

export async function submitProfileTurnStream(
  sessionUuid: string,
  content: string,
  onEvent: (event: ProfileTurnStreamEvent) => void,
) {
  const response = await fetch(
    buildApiUrl(`/sessions/${sessionUuid}/profile/turns/stream`),
    {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({ content }),
    },
  );
  await consumeStream(response, onEvent);
}

export async function submitAssessmentTurn(
  sessionUuid: string,
  payload: SubmitTurnPayload,
) {
  const response = await api.post<SubmitTurnResponse>(`/sessions/${sessionUuid}/turns`, {
    content_type: "scenario_answer",
    ...payload,
  });
  return response.data;
}

export async function submitAssessmentTurnStream(
  sessionUuid: string,
  payload: SubmitTurnPayload,
  onEvent: (event: SubmitTurnStreamEvent) => void,
) {
  const response = await fetch(buildApiUrl(`/sessions/${sessionUuid}/turns/stream`), {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify({
      content_type: "scenario_answer",
      ...payload,
    }),
  });

  await consumeStream(response, onEvent);
}

export async function startAssessmentInterviewStream(
  sessionUuid: string,
  onEvent: (event: SubmitTurnStreamEvent) => void,
) {
  const response = await fetch(
    buildApiUrl(`/sessions/${sessionUuid}/interview/start/stream`),
    { method: "POST", headers: buildHeaders() },
  );
  await consumeStream(response, onEvent);
}

export async function finishAssessmentSession(sessionUuid: string) {
  const response = await api.post<FinishSessionResponse>(`/sessions/${sessionUuid}/finish`);
  return response.data;
}

export async function skipCurrentAssessmentStage(sessionUuid: string) {
  const response = await api.post<SkipStageResponse>(
    `/sessions/${sessionUuid}/stages/current/skip`,
  );
  return response.data;
}

export async function continueCurrentAssessmentStage(sessionUuid: string) {
  const response = await api.post<ContinueStageResponse>(
    `/sessions/${sessionUuid}/stages/current/continue`,
  );
  return response.data;
}

export async function updateAssessmentLanguageMode(
  sessionUuid: string,
  languageMode: "standard" | "plain",
) {
  const response = await api.patch<LanguageModeResponse>(
    `/sessions/${sessionUuid}/language-mode`,
    { language_mode: languageMode },
  );
  return response.data;
}

export async function getAssessmentReport(sessionUuid: string) {
  const response = await api.get<AssessmentReportResponse>(`/sessions/${sessionUuid}/report`);
  return response.data;
}

export async function requestAssessmentReportGeneration(sessionUuid: string) {
  const response = await api.post<{
    session_uuid: string;
    status: "ready" | "scheduled" | "running" | "failed";
  }>(`/sessions/${sessionUuid}/report/generate`);
  return response.data;
}

export async function submitAssessmentFeedback(
  sessionUuid: string,
  payload: SubmitFeedbackPayload,
) {
  const response = await api.post<FeedbackResponse>(`/sessions/${sessionUuid}/feedback`, payload);
  return response.data;
}

export async function getAssessmentFeedback(sessionUuid: string) {
  const response = await api.get<FeedbackStateResponse>(`/sessions/${sessionUuid}/feedback`);
  return response.data;
}

export async function downloadAssessmentReportPdf(sessionUuid: string) {
  const timezone =
    Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  const response = await api.get<Blob>(`/sessions/${sessionUuid}/report.pdf`, {
    responseType: "blob",
    params: { timezone },
  });
  return response.data;
}

function buildApiUrl(path: string) {
  const baseURL = api.defaults.baseURL || "";
  const normalizedBase = String(baseURL).replace(/\/$/, "");
  if (normalizedBase.startsWith("http://") || normalizedBase.startsWith("https://")) {
    return `${normalizedBase}${path}`;
  }
  return `${window.location.origin}${normalizedBase}${path}`;
}

function buildHeaders() {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = localStorage.getItem("admin_token");
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function consumeStream<T extends { event: string; detail?: string; message?: string }>(
  response: Response,
  onEvent: (event: T) => void,
) {
  if (!response.ok) {
    throw new Error(`Stream request failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Stream response body is not available.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) handleStreamLine(line, onEvent);
  }
  buffer += decoder.decode();
  handleStreamLine(buffer, onEvent);
}

function handleStreamLine<T extends { event: string; detail?: string; message?: string }>(
  line: string,
  onEvent: (event: T) => void,
) {
  const trimmed = line.trim();
  if (!trimmed) return;
  const event = JSON.parse(trimmed) as T;
  onEvent(event);
  if (event.event === "error") {
    throw new Error(event.detail || event.message || "Stream event returned error.");
  }
}
