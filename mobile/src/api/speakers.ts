import { uint8ArrayToBase64 } from '../pcm';
import { authHeaders, httpUrl, type Settings } from '../settings';

export { uint8ArrayToBase64 };

export type SpeakerRole = 'owner' | 'member' | 'guest';

export type SpeakerSummary = {
  id: string;
  name: string;
  role: SpeakerRole;
  samples_count: number;
  created_at: string;
  /** Đã đọc đủ số câu mẫu chưa. Còn false thì màn hình đăng ký vẫn phải hiện. */
  complete?: boolean;
  remaining_samples?: number;
};

export type EnrollmentPhrase = {
  index: number;
  text: string;
};

/**
 * Kịch bản đăng ký giọng do server quyết định.
 *
 * Các câu mẫu KHÔNG nằm trong app: chúng mang tên trợ lý, nên đổi tên trợ lý ở
 * server là đổi luôn nội dung người dùng phải đọc, không cần phát hành lại app.
 */
export type EnrollmentPlan = {
  required: boolean;
  satisfied: boolean;
  assistant_name: string;
  wake_word_enabled: boolean;
  verification_enabled: boolean;
  required_samples: number;
  completed_samples: number;
  phrases: EnrollmentPhrase[];
  sample_rate: number;
  channels: number;
  encoding: string;
  min_sample_ms: number;
  enrolled_total: number;
};

export type SpeakersListResponse = {
  enabled: boolean;
  threshold: number;
  total: number;
  speakers: SpeakerSummary[];
};

export type VerifySpeakerResponse = {
  verified: boolean;
  speaker_id: string | null;
  speaker_name: string | null;
  role: string | null;
  score: number;
  threshold: number;
  reason: string;
};

export async function fetchSpeakers(settings: Settings): Promise<SpeakersListResponse> {
  const url = httpUrl(settings, '/api/v1/speakers');
  const res = await fetch(url, {
    method: 'GET',
    headers: {
      ...authHeaders(settings),
    },
  });
  if (!res.ok) {
    throw new Error(`Không lấy được danh sách giọng nói (${res.status})`);
  }
  return (await res.json()) as SpeakersListResponse;
}

export async function fetchEnrollmentPlan(
  settings: Settings,
  speakerId?: string
): Promise<EnrollmentPlan> {
  const query = speakerId ? `?speaker_id=${encodeURIComponent(speakerId)}` : '';
  const url = httpUrl(settings, `/api/v1/speakers/enrollment${query}`);
  const res = await fetch(url, { method: 'GET', headers: { ...authHeaders(settings) } });
  if (!res.ok) {
    throw new Error(`Không lấy được hướng dẫn đăng ký giọng nói (${res.status})`);
  }
  return (await res.json()) as EnrollmentPlan;
}

export async function enrollSpeaker(
  settings: Settings,
  speakerId: string,
  name: string,
  role: SpeakerRole,
  pcmBytes: Uint8Array,
  phraseIndex?: number
): Promise<SpeakerSummary> {
  const url = httpUrl(settings, '/api/v1/speakers/enroll');
  const base64Audio = uint8ArrayToBase64(pcmBytes);
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(settings),
    },
    body: JSON.stringify({
      speaker_id: speakerId,
      name,
      role,
      audio_base64: base64Audio,
      phrase_index: phraseIndex,
    }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as { detail?: string } | null;
    const detail = data?.detail || `HTTP ${res.status}`;
    throw new Error(`Đăng ký giọng nói thất bại: ${detail}`);
  }
  return (await res.json()) as SpeakerSummary;
}

export async function verifySpeakerAudio(
  settings: Settings,
  pcmBytes: Uint8Array,
  threshold?: number
): Promise<VerifySpeakerResponse> {
  const url = httpUrl(settings, '/api/v1/speakers/verify');
  const base64Audio = uint8ArrayToBase64(pcmBytes);
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(settings),
    },
    body: JSON.stringify({
      audio_base64: base64Audio,
      threshold,
    }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as { detail?: string } | null;
    const detail = data?.detail || `HTTP ${res.status}`;
    throw new Error(`Xác thực giọng nói thất bại: ${detail}`);
  }
  return (await res.json()) as VerifySpeakerResponse;
}

export async function deleteSpeakerProfile(
  settings: Settings,
  speakerId: string
): Promise<boolean> {
  const url = httpUrl(settings, `/api/v1/speakers/${encodeURIComponent(speakerId)}`);
  const res = await fetch(url, {
    method: 'DELETE',
    headers: {
      ...authHeaders(settings),
    },
  });
  if (!res.ok) {
    throw new Error(`Không thể xoá hồ sơ giọng nói (${res.status})`);
  }
  return true;
}

export async function updateSpeakerSettings(
  settings: Settings,
  enabled?: boolean,
  threshold?: number
): Promise<{ enabled: boolean; threshold: number }> {
  const url = httpUrl(settings, '/api/v1/speakers/settings');
  const res = await fetch(url, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(settings),
    },
    body: JSON.stringify({
      enabled,
      threshold,
    }),
  });
  if (!res.ok) {
    throw new Error(`Không cập nhật được cài đặt giọng nói (${res.status})`);
  }
  return (await res.json()) as { enabled: boolean; threshold: number };
}
