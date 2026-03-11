#include <Arduino.h>

static const uint32_t REED_PIN = 4;
static const uint32_t DEBOUNCE_MS = 100;
#define LOGIC_INVERT 0

enum DoorState : uint8_t {
  DOOR_CLOSED = 0,
  DOOR_OPEN = 1,
};

static TaskHandle_t gDoorTaskHandle = nullptr;
static volatile uint32_t gEventSeq = 1;

static DoorState readDoorState() {
  const int raw = digitalRead(REED_PIN);
  const bool activeHighOpen = (LOGIC_INVERT != 0);
  if (activeHighOpen) {
    return raw == LOW ? DOOR_OPEN : DOOR_CLOSED;
  }
  return raw == HIGH ? DOOR_OPEN : DOOR_CLOSED;
}

static void emitSessionEvent(const char *eventName) {
  const uint32_t seq = gEventSeq++;
  const uint32_t tMs = millis();
  Serial.print("FRIDGE,EVENT,");
  Serial.print(eventName);
  Serial.print(",seq=");
  Serial.print(seq);
  Serial.print(",t_ms=");
  Serial.println(tMs);
}

void IRAM_ATTR reedPinISR() {
  BaseType_t higherPriorityTaskWoken = pdFALSE;
  if (gDoorTaskHandle != nullptr) {
    vTaskNotifyGiveFromISR(gDoorTaskHandle, &higherPriorityTaskWoken);
  }
  if (higherPriorityTaskWoken) {
    portYIELD_FROM_ISR();
  }
}

void doorTask(void *parameter) {
  (void)parameter;

  DoorState raw_state = readDoorState();
  DoorState stable_state = raw_state;
  DoorState candidate_state = stable_state;
  TickType_t candidate_since_tick = xTaskGetTickCount();

  for (;;) {
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));

    raw_state = readDoorState();
    if (raw_state == stable_state) {
      candidate_state = stable_state;
      candidate_since_tick = xTaskGetTickCount();
      vTaskDelay(pdMS_TO_TICKS(1));
      continue;
    }

    if (raw_state != candidate_state) {
      candidate_state = raw_state;
      candidate_since_tick = xTaskGetTickCount();
      vTaskDelay(pdMS_TO_TICKS(1));
      continue;
    }

    const TickType_t now = xTaskGetTickCount();
    if ((now - candidate_since_tick) >= pdMS_TO_TICKS(DEBOUNCE_MS)) {
      stable_state = candidate_state;
      if (stable_state == DOOR_OPEN) {
        emitSessionEvent("SESSION_START");
      } else {
        emitSessionEvent("SESSION_END");
      }
    }

    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(REED_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(REED_PIN), reedPinISR, CHANGE);

  Serial.println("FRIDGE,BOOT,ready");

  xTaskCreate(
      doorTask,
      "doorTask",
      2048,
      nullptr,
      2,
      &gDoorTaskHandle);
}

void loop() {
  vTaskDelay(pdMS_TO_TICKS(1000));
}