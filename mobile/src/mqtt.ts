/**
 * MQTT WebSocket client for direct hardware monitoring and control.
 *
 * Connects directly to the MQTT broker (e.g. HiveMQ Cloud TLS port 8884 or
 * local Mosquitto WS port 9001) over WebSocket.
 *
 * Subscribes to:
 *   home/+/+/state          (authoritative device attributes)
 *   home/+/+/state/+        (single-attribute updates)
 *   home/+/+/availability   (device online/offline status)
 *
 * Publishes commands to:
 *   home/<room>/<device>/set
 *   home/<room>/<device>/set/<cap>
 */

import { Client, Message } from 'paho-mqtt';
import type { MqttSettings } from './settings';

export type MqttConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export type MqttMessageCallback = (topic: string, payload: string) => void;
export type MqttStatusCallback = (status: MqttConnectionStatus, error?: string) => void;

class MqttManager {
  private client: Client | null = null;
  private status: MqttConnectionStatus = 'disconnected';
  private config: MqttSettings | null = null;
  private messageListeners = new Set<MqttMessageCallback>();
  private statusListeners = new Set<MqttStatusCallback>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldStayConnected = false;
  private reconnectAttempts = 0;

  getStatus(): MqttConnectionStatus {
    return this.status;
  }

  isConnected(): boolean {
    return this.status === 'connected' && !!this.client?.isConnected();
  }

  onStatusChange(cb: MqttStatusCallback): () => void {
    this.statusListeners.add(cb);
    cb(this.status);
    return () => this.statusListeners.delete(cb);
  }

  onMessage(cb: MqttMessageCallback): () => void {
    this.messageListeners.add(cb);
    return () => this.messageListeners.delete(cb);
  }

  private setStatus(next: MqttConnectionStatus, error?: string): void {
    this.status = next;
    for (const listener of this.statusListeners) {
      listener(next, error);
    }
  }

  connect(config: MqttSettings): void {
    if (!config.enabled || !config.host.trim()) {
      this.disconnect();
      return;
    }

    this.config = config;
    this.shouldStayConnected = true;

    if (this.client?.isConnected()) {
      return;
    }

    this.setStatus('connecting');
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    try {
      const clientId = `mob_${Math.random().toString(36).slice(2, 8)}`;
      const cleanHost = config.host.trim().replace(/^https?:\/\/|^wss?:\/\//i, '');
      const path = config.path.trim() ? (config.path.startsWith('/') ? config.path : `/${config.path}`) : '/mqtt';
      const port = Number(config.port) || (config.ssl ? 8884 : 9001);

      this.client = new Client(cleanHost, port, path, clientId);

      this.client.onConnectionLost = (response) => {
        const errorMsg = response.errorMessage || 'Mất kết nối MQTT';
        console.warn('[MQTT] Connection lost:', errorMsg);
        this.setStatus('disconnected', errorMsg);
        if (this.shouldStayConnected) {
          this.scheduleReconnect();
        }
      };

      this.client.onMessageArrived = (message: Message) => {
        const topic = message.destinationName;
        const payload = message.payloadString;
        for (const listener of this.messageListeners) {
          try {
            listener(topic, payload);
          } catch (err) {
            console.error('[MQTT] Error in message listener:', err);
          }
        }
      };

      const connectOptions: any = {
        timeout: 10,
        keepAliveInterval: 30,
        cleanSession: true,
        useSSL: !!config.ssl,
        onSuccess: () => {
          console.log('[MQTT] Connected to broker successfully!');
          this.reconnectAttempts = 0;
          this.setStatus('connected');
          this.subscribeTopics(config.baseTopic || 'home');
        },
        onFailure: (err: any) => {
          const errMsg = err?.errorMessage || err?.message || 'Kết nối thất bại';
          console.warn('[MQTT] Connect failed:', errMsg);
          this.setStatus('error', errMsg);
          if (this.shouldStayConnected) {
            this.scheduleReconnect();
          }
        },
      };

      if (config.user.trim()) {
        connectOptions.userName = config.user.trim();
      }
      if (config.pass.trim()) {
        connectOptions.password = config.pass.trim();
      }

      this.client.connect(connectOptions);
    } catch (err: any) {
      const msg = String(err?.message || err);
      console.error('[MQTT] Exception while initiating connection:', msg);
      this.setStatus('error', msg);
      this.scheduleReconnect();
    }
  }

  private subscribeTopics(base: string): void {
    if (!this.client?.isConnected()) return;

    const topics = [
      `${base}/+/+/state`,
      `${base}/+/+/state/+`,
      `${base}/+/+/availability`,
      `${base}/+/availability`,
      `${base}/esp32s3/availability`,
    ];

    for (const t of topics) {
      try {
        this.client.subscribe(t, {
          qos: 1,
          onSuccess: () => console.log(`[MQTT] Subscribed: ${t}`),
          onFailure: (e) => console.warn(`[MQTT] Subscribe failed: ${t}`, e),
        });
      } catch (e) {
        console.warn(`[MQTT] Exception subscribing to ${t}:`, e);
      }
    }
  }

  private scheduleReconnect(): void {
    if (!this.shouldStayConnected || this.reconnectTimer) return;
    this.reconnectAttempts += 1;
    const delay = Math.min(3000 * Math.pow(1.5, Math.min(this.reconnectAttempts, 5)), 20000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.config && this.shouldStayConnected) {
        console.log(`[MQTT] Reconnecting (attempt ${this.reconnectAttempts})...`);
        this.connect(this.config);
      }
    }, delay);
  }

  disconnect(): void {
    this.shouldStayConnected = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.client?.isConnected()) {
      try {
        this.client.disconnect();
      } catch {}
    }
    this.client = null;
    this.setStatus('disconnected');
  }

  /**
   * Publish an atomic command down to hardware via MQTT.
   */
  publishCommand(room: string, deviceId: string, setValues: Record<string, any>): boolean {
    if (!this.client?.isConnected()) {
      console.warn('[MQTT] Cannot publish command: client is not connected');
      return false;
    }

    const base = this.config?.baseTopic || 'home';
    const jsonTopic = `${base}/${room}/${deviceId}/set`;

    const payload = JSON.stringify({
      id: `mob_${Date.now()}`,
      ts: new Date().toISOString(),
      device_id: deviceId,
      set: setValues,
    });

    try {
      const msg = new Message(payload);
      msg.destinationName = jsonTopic;
      msg.qos = 1;
      msg.retained = false;
      this.client.send(msg);

      console.log(`[MQTT-TX] Sent command to ${jsonTopic}:`, payload);
      return true;
    } catch (err) {
      console.error('[MQTT-TX] Publish failed:', err);
      return false;
    }
  }

  /**
   * Publish raw payload directly to an MQTT topic.
   */
  publishRaw(topic: string, payload: string, retained = false, qos: 0 | 1 = 1): boolean {
    if (!this.client?.isConnected()) return false;
    try {
      const msg = new Message(payload);
      msg.destinationName = topic;
      msg.qos = qos;
      msg.retained = retained;
      this.client.send(msg);
      return true;
    } catch (err) {
      console.error(`[MQTT-TX] Failed to publish to ${topic}:`, err);
      return false;
    }
  }
}

export const mqttManager = new MqttManager();
