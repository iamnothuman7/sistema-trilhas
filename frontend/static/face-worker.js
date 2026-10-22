const vision = import('/static/vendor/mediapipe/vision_bundle.js');

let task;
async function ready() {
  if (!task) task = vision.then(async ({FaceLandmarker, FilesetResolver}) => {
    const files = await FilesetResolver.forVisionTasks('/static/vendor/mediapipe/wasm');
    return FaceLandmarker.createFromOptions(files, {
      baseOptions: {modelAssetPath: '/static/vendor/mediapipe/face_landmarker.task', delegate: 'CPU'},
      runningMode: 'IMAGE', numFaces: 3,
      minFaceDetectionConfidence: .5, minFacePresenceConfidence: .5,
      outputFaceBlendshapes: false, outputFacialTransformationMatrixes: false,
    });
  });
  return task;
}

self.onmessage = async ({data}) => {
  const {id, bitmap} = data;
  try {
    const detector = await ready();
    if (!bitmap) { self.postMessage({id, ready: true}); return; }
    const started = performance.now();
    const result = detector.detect(bitmap);
    const faces = result.faceLandmarks.map(points => {
      const xs = points.map(point => point.x), ys = points.map(point => point.y);
      const left = Math.min(...xs), right = Math.max(...xs);
      const top = Math.min(...ys), bottom = Math.max(...ys);
      return {left, right, top, bottom, centerX: (left + right) / 2, eyeY: (points[33].y + points[263].y) / 2};
    });
    self.postMessage({id, faces, inferenceMs: performance.now() - started});
  } catch (error) {
    task = null;
    self.postMessage({id, error: 'face-unavailable', detail: String(error?.message || error)});
  } finally { bitmap?.close(); }
};
