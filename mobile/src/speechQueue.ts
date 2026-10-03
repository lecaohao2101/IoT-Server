/**
 * Plays the assistant's reply as it arrives.
 *
 * The server synthesises one clause at a time and sends each as a complete MP3
 * in its own binary frame, so the first words play while the model is still
 * finishing the sentence. WebSocket preserves message boundaries, which is what
 * makes "one frame = one playable file" safe.
 *
 * Clauses must play in order and without overlap, so this is a strict queue:
 * one player at a time, the next starting only when the previous reports
 * `didJustFinish`.
 */

import { createAudioPlayer, type AudioPlayer } from 'expo-audio';
import { Directory, File, Paths } from 'expo-file-system';

const CACHE_DIR = 'speech';
/** A clause that never reports completion must not wedge the queue forever. */
const PLAYBACK_TIMEOUT_MS = 20000;

type QueueItem = { file: File };

export class SpeechQueue {
  private queue: QueueItem[] = [];
  private player: AudioPlayer | null = null;
  private current: File | null = null;
  private draining = false;
  private stopped = false;
  private counter = 0;

  constructor(private readonly onSpeakingChange: (speaking: boolean) => void) {}

  /** Hand over one complete encoded clause (MP3, or WAV for the mock provider). */
  enqueue(bytes: Uint8Array, extension: string): void {
    if (this.stopped || bytes.byteLength === 0) return;
    try {
      const dir = new Directory(Paths.cache, CACHE_DIR);
      if (!dir.exists) dir.create({ intermediates: true });

      this.counter += 1;
      const file = new File(dir, `clause-${Date.now()}-${this.counter}.${extension}`);
      if (file.exists) file.delete();
      file.create();
      file.write(bytes);

      this.queue.push({ file });
      void this.drain();
    } catch (error) {
      // Losing one clause is better than losing the turn.
      console.warn('speech: could not buffer clause', error);
    }
  }

  /** Stop immediately and drop anything pending -- used for barge-in. */
  stop(): void {
    this.stopped = true;
    this.releasePlayer();
    for (const item of this.queue) this.safeDelete(item.file);
    this.queue = [];
    this.onSpeakingChange(false);
  }

  /** Re-arm after a `stop()`, before the next turn. */
  reset(): void {
    this.stopped = false;
  }

  private async drain(): Promise<void> {
    if (this.draining) return;
    this.draining = true;
    try {
      while (!this.stopped) {
        const item = this.queue.shift();
        if (!item) break;
        await this.playOne(item.file);
      }
    } finally {
      this.draining = false;
      if (!this.queue.length) this.onSpeakingChange(false);
    }
  }

  private playOne(file: File): Promise<void> {
    return new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        subscription?.remove();
        this.releasePlayer();
        this.safeDelete(file);
        resolve();
      };

      const timer = setTimeout(finish, PLAYBACK_TIMEOUT_MS);
      let subscription: { remove: () => void } | undefined;

      try {
        const player = createAudioPlayer(file.uri);
        this.player = player;
        this.current = file;
        this.onSpeakingChange(true);

        subscription = player.addListener('playbackStatusUpdate', (status) => {
          if (status.didJustFinish) finish();
        });
        player.play();
      } catch (error) {
        console.warn('speech: playback failed', error);
        finish();
      }
    });
  }

  private releasePlayer(): void {
    const player = this.player;
    this.player = null;
    if (player) {
      try {
        player.pause();
        player.remove();
      } catch {
        // The player may already be torn down; nothing useful to do.
      }
    }
    if (this.current) {
      this.safeDelete(this.current);
      this.current = null;
    }
  }

  private safeDelete(file: File): void {
    try {
      if (file.exists) file.delete();
    } catch {
      // Cache files; the OS reclaims them.
    }
  }
}
