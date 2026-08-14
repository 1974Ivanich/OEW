#ifndef MAP_CAPTURE_PORT_H
#define MAP_CAPTURE_PORT_H

/* Тонкий production-порт (README «Обязательный protection и interlock port»):
 * единственное место, связывающее generic map_capture с проектными
 * PWM/FOC/V/f/protection API. Вызывается один раз при старте. */
int MapCapturePort_Init(void);

#endif /* MAP_CAPTURE_PORT_H */
