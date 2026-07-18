package cz.filip.avsyncmeter;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTimestamp;
import android.media.Image;
import android.media.ImageFormat;
import android.media.ImageReader;
import android.media.MediaRecorder;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Process;
import android.provider.Settings;
import android.util.Range;
import android.util.Size;
import android.view.Gravity;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.nio.ByteBuffer;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.Deque;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity implements TextureView.SurfaceTextureListener {
    private static final int PERMISSION_REQUEST = 42;
    private static final int SAMPLE_RATE = 48_000;
    private static final long EVENT_REFRACTORY_NS = 550_000_000L;
    private static final long MAX_PAIR_DISTANCE_NS = 450_000_000L;

    private TextureView textureView;
    private TextView statusView;
    private TextView resultView;
    private TextView countersView;
    private TextView diagnosticsView;
    private EditText distanceEdit;
    private Button startStopButton;
    private Button resetButton;

    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private CameraDevice cameraDevice;
    private CameraCaptureSession captureSession;
    private ImageReader imageReader;
    private Surface previewSurface;
    private Size analysisSize;
    private boolean cameraTimestampRealtime;

    private Thread audioThread;
    private AudioRecord audioRecord;
    private volatile boolean measuring;
    private volatile double distanceMeters = 2.5;

    private final Object detectionLock = new Object();
    private final Deque<Long> flashEvents = new ArrayDeque<>();
    private final Deque<Long> audioEvents = new ArrayDeque<>();
    private final List<Double> measurementsMs = new ArrayList<>();

    private double brightnessBaseline = -1.0;
    private double lastBrightness = -1.0;
    private boolean flashArmed = true;
    private long lastFlashNs = Long.MIN_VALUE;
    private long lastAudioNs = Long.MIN_VALUE;
    private int flashCount;
    private int audioCount;
    private double lastBrightnessValue;
    private double lastAudioRms;
    private double noiseRms = 250.0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(buildUi());
        textureView.setSurfaceTextureListener(this);
        updateUi();
    }

    private View buildUi() {
        int pad = dp(16);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(pad, pad, pad, dp(28));

        TextView title = new TextView(this);
        title.setText("AV Sync Meter");
        title.setTextSize(27);
        title.setTextColor(Color.WHITE);
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        title.setPadding(0, 0, 0, dp(6));
        content.addView(title, matchWrap());

        TextView intro = new TextView(this);
        intro.setText("Namíř kameru na TV, telefon nech v místě poslechu a spusť testovací MKV. Aplikace porovná okamžik bílého záblesku se skutečným zvukem ze soundbaru.");
        intro.setTextSize(15);
        intro.setTextColor(Color.LTGRAY);
        intro.setGravity(Gravity.CENTER_HORIZONTAL);
        intro.setPadding(0, 0, 0, dp(12));
        content.addView(intro, matchWrap());

        FrameLayout cameraFrame = new FrameLayout(this);
        cameraFrame.setBackgroundColor(Color.BLACK);
        textureView = new TextureView(this);
        cameraFrame.addView(textureView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        cameraFrame.addView(new TargetOverlay(this), new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        LinearLayout.LayoutParams cameraParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(245));
        cameraParams.bottomMargin = dp(12);
        content.addView(cameraFrame, cameraParams);

        LinearLayout distanceRow = new LinearLayout(this);
        distanceRow.setOrientation(LinearLayout.HORIZONTAL);
        distanceRow.setGravity(Gravity.CENTER_VERTICAL);

        TextView distanceLabel = new TextView(this);
        distanceLabel.setText("Vzdálenost telefonu od soundbaru (m):");
        distanceLabel.setTextSize(15);
        distanceLabel.setTextColor(Color.WHITE);
        distanceRow.addView(distanceLabel, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        distanceEdit = new EditText(this);
        distanceEdit.setText("2.5");
        distanceEdit.setInputType(android.text.InputType.TYPE_CLASS_NUMBER | android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL);
        distanceEdit.setTextColor(Color.WHITE);
        distanceEdit.setGravity(Gravity.CENTER);
        distanceRow.addView(distanceEdit, new LinearLayout.LayoutParams(dp(86), LinearLayout.LayoutParams.WRAP_CONTENT));
        content.addView(distanceRow, matchWrap());

        startStopButton = new Button(this);
        startStopButton.setText("ZAHÁJIT MĚŘENÍ");
        startStopButton.setTextSize(17);
        startStopButton.setOnClickListener(v -> {
            if (measuring) {
                stopMeasurement();
            } else {
                beginAfterPermissionCheck();
            }
        });
        LinearLayout.LayoutParams buttonParams = matchWrap();
        buttonParams.topMargin = dp(10);
        content.addView(startStopButton, buttonParams);

        resetButton = new Button(this);
        resetButton.setText("Vynulovat výsledky");
        resetButton.setOnClickListener(v -> resetMeasurements());
        content.addView(resetButton, matchWrap());

        statusView = makeInfoText(15, Color.LTGRAY);
        statusView.setPadding(0, dp(8), 0, dp(8));
        content.addView(statusView, matchWrap());

        resultView = makeInfoText(21, Color.WHITE);
        resultView.setGravity(Gravity.CENTER_HORIZONTAL);
        resultView.setPadding(dp(8), dp(14), dp(8), dp(14));
        resultView.setBackgroundColor(Color.rgb(28, 38, 48));
        content.addView(resultView, matchWrap());

        countersView = makeInfoText(15, Color.WHITE);
        countersView.setPadding(0, dp(10), 0, dp(4));
        content.addView(countersView, matchWrap());

        diagnosticsView = makeInfoText(13, Color.GRAY);
        content.addView(diagnosticsView, matchWrap());

        TextView note = makeInfoText(13, Color.LTGRAY);
        note.setText("Výsledek > 0 ms znamená, že zvuk přichází po obrazu. Výsledek < 0 ms znamená, že zvuk přichází před obrazem. Korekce vzdálenosti odečítá dobu letu zvuku vzduchem (přibližně 2,9 ms na metr). Spolehlivý rozsah párování je přibližně ±450 ms.");
        note.setPadding(0, dp(14), 0, 0);
        content.addView(note, matchWrap());

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.rgb(15, 20, 25));
        scroll.addView(content);
        return scroll;
    }

    private TextView makeInfoText(float size, int color) {
        TextView view = new TextView(this);
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void beginAfterPermissionCheck() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO}, PERMISSION_REQUEST);
            return;
        }
        startMeasurement();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST) {
            boolean granted = grantResults.length == 2
                    && grantResults[0] == PackageManager.PERMISSION_GRANTED
                    && grantResults[1] == PackageManager.PERMISSION_GRANTED;
            if (granted) {
                startMeasurement();
            } else {
                Toast.makeText(this, "Bez kamery a mikrofonu nelze A/V posun změřit.", Toast.LENGTH_LONG).show();
            }
        }
    }

    private void startMeasurement() {
        if (measuring) return;
        distanceMeters = parseDistance();
        resetDetectorState(false);
        measuring = true;
        startStopButton.setText("ZASTAVIT MĚŘENÍ");
        distanceEdit.setEnabled(false);
        statusView.setText("Spouštím kameru a mikrofon…");
        startCameraThread();
        if (textureView.isAvailable()) {
            openCamera();
        }
        startAudioCapture();
        updateUi();
    }

    private double parseDistance() {
        try {
            String value = distanceEdit.getText().toString().trim().replace(',', '.');
            return Math.max(0.0, Math.min(20.0, Double.parseDouble(value)));
        } catch (Exception ignored) {
            distanceEdit.setText("2.5");
            return 2.5;
        }
    }

    private void stopMeasurement() {
        measuring = false;
        startStopButton.setText("ZAHÁJIT MĚŘENÍ");
        distanceEdit.setEnabled(true);
        stopAudioCapture();
        closeCamera();
        stopCameraThread();
        statusView.setText("Měření zastaveno.");
        updateUi();
    }

    private void resetMeasurements() {
        synchronized (detectionLock) {
            measurementsMs.clear();
            flashEvents.clear();
            audioEvents.clear();
            flashCount = 0;
            audioCount = 0;
        }
        resetDetectorState(true);
        updateUi();
    }

    private void resetDetectorState(boolean keepMeasuring) {
        brightnessBaseline = -1.0;
        lastBrightness = -1.0;
        flashArmed = true;
        lastFlashNs = Long.MIN_VALUE;
        lastAudioNs = Long.MIN_VALUE;
        noiseRms = 250.0;
        lastBrightnessValue = 0.0;
        lastAudioRms = 0.0;
        synchronized (detectionLock) {
            flashEvents.clear();
            audioEvents.clear();
        }
        if (!keepMeasuring && !measuring) {
            // No-op. This branch keeps the detector reset explicit for a new run.
        }
    }

    private void startCameraThread() {
        if (cameraThread != null) return;
        cameraThread = new HandlerThread("AVSyncCamera", Process.THREAD_PRIORITY_DISPLAY);
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());
    }

    private void stopCameraThread() {
        if (cameraThread == null) return;
        cameraThread.quitSafely();
        try {
            cameraThread.join(1000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        cameraThread = null;
        cameraHandler = null;
    }

    private void openCamera() {
        if (!measuring || cameraDevice != null || !textureView.isAvailable()) return;
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) return;
        try {
            CameraManager manager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
            String cameraId = chooseBackCamera(manager);
            CameraCharacteristics characteristics = manager.getCameraCharacteristics(cameraId);
            Integer timestampSource = characteristics.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE);
            cameraTimestampRealtime = timestampSource != null
                    && timestampSource == CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME;
            analysisSize = chooseAnalysisSize(characteristics);
            imageReader = ImageReader.newInstance(
                    analysisSize.getWidth(), analysisSize.getHeight(), ImageFormat.YUV_420_888, 3);
            imageReader.setOnImageAvailableListener(this::onImageAvailable, cameraHandler);
            manager.openCamera(cameraId, cameraStateCallback, cameraHandler);
        } catch (CameraAccessException | IllegalStateException e) {
            showError("Kameru se nepodařilo otevřít: " + e.getMessage());
        }
    }

    private String chooseBackCamera(CameraManager manager) throws CameraAccessException {
        String fallback = manager.getCameraIdList()[0];
        for (String id : manager.getCameraIdList()) {
            Integer facing = manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING);
            if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) return id;
        }
        return fallback;
    }

    private Size chooseAnalysisSize(CameraCharacteristics characteristics) {
        StreamConfigurationMap map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        if (map == null) return new Size(640, 480);
        Size[] sizes = map.getOutputSizes(ImageFormat.YUV_420_888);
        if (sizes == null || sizes.length == 0) return new Size(640, 480);
        List<Size> candidates = new ArrayList<>(Arrays.asList(sizes));
        candidates.sort(Comparator.comparingLong(s -> (long) s.getWidth() * s.getHeight()));
        for (Size size : candidates) {
            if (size.getWidth() >= 640 && size.getHeight() >= 480) return size;
        }
        return candidates.get(candidates.size() - 1);
    }

    private final CameraDevice.StateCallback cameraStateCallback = new CameraDevice.StateCallback() {
        @Override
        public void onOpened(CameraDevice camera) {
            cameraDevice = camera;
            createCaptureSession();
        }

        @Override
        public void onDisconnected(CameraDevice camera) {
            camera.close();
            cameraDevice = null;
            showError("Kamera byla odpojena.");
        }

        @Override
        public void onError(CameraDevice camera, int error) {
            camera.close();
            cameraDevice = null;
            showError("Chyba kamery: " + error);
        }
    };

    private void createCaptureSession() {
        if (cameraDevice == null || imageReader == null || !textureView.isAvailable()) return;
        try {
            SurfaceTexture texture = textureView.getSurfaceTexture();
            if (texture == null) return;
            texture.setDefaultBufferSize(analysisSize.getWidth(), analysisSize.getHeight());
            previewSurface = new Surface(texture);

            CaptureRequest.Builder request = cameraDevice.createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
            request.addTarget(previewSurface);
            request.addTarget(imageReader.getSurface());
            request.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO);
            request.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON);
            Range<Integer> fps = chooseFpsRange(cameraDevice.getId());
            if (fps != null) request.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, fps);

            cameraDevice.createCaptureSession(
                    Arrays.asList(previewSurface, imageReader.getSurface()),
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession session) {
                            if (!measuring || cameraDevice == null) {
                                session.close();
                                return;
                            }
                            captureSession = session;
                            try {
                                session.setRepeatingRequest(request.build(), null, cameraHandler);
                                runOnUiThread(() -> statusView.setText(
                                        "Měřím. Drž bílý záblesk uvnitř rámečku a nech přehrát alespoň 8–10 pípnutí."));
                            } catch (CameraAccessException e) {
                                showError("Náhled kamery nelze spustit: " + e.getMessage());
                            }
                        }

                        @Override
                        public void onConfigureFailed(CameraCaptureSession session) {
                            showError("Konfigurace kamery selhala.");
                        }
                    }, cameraHandler);
        } catch (CameraAccessException e) {
            showError("Chyba konfigurace kamery: " + e.getMessage());
        }
    }

    private Range<Integer> chooseFpsRange(String cameraId) {
        try {
            CameraManager manager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
            Range<Integer>[] ranges = manager.getCameraCharacteristics(cameraId)
                    .get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES);
            if (ranges == null || ranges.length == 0) return null;
            Range<Integer> best = ranges[0];
            for (Range<Integer> range : ranges) {
                if (range.getUpper() > best.getUpper()
                        || (range.getUpper().equals(best.getUpper()) && range.getLower() > best.getLower())) {
                    best = range;
                }
            }
            return best;
        } catch (Exception ignored) {
            return null;
        }
    }

    private void onImageAvailable(ImageReader reader) {
        Image image = null;
        try {
            image = reader.acquireLatestImage();
            if (image == null || !measuring) return;
            double brightness = calculateCentralLuma(image);
            long timestampNs = cameraTimestampRealtime ? image.getTimestamp() : System.nanoTime();
            processBrightness(brightness, timestampNs);
        } catch (Exception ignored) {
            // A dropped analysis frame is harmless; the next flash repeats after one second.
        } finally {
            if (image != null) image.close();
        }
    }

    private double calculateCentralLuma(Image image) {
        Image.Plane plane = image.getPlanes()[0];
        ByteBuffer buffer = plane.getBuffer();
        int rowStride = plane.getRowStride();
        int pixelStride = plane.getPixelStride();
        int width = image.getWidth();
        int height = image.getHeight();
        int x0 = width / 4;
        int x1 = width * 3 / 4;
        int y0 = height / 4;
        int y1 = height * 3 / 4;
        long sum = 0;
        int count = 0;
        int limit = buffer.limit();
        for (int y = y0; y < y1; y += 4) {
            int row = y * rowStride;
            for (int x = x0; x < x1; x += 4) {
                int index = row + x * pixelStride;
                if (index >= 0 && index < limit) {
                    sum += buffer.get(index) & 0xFF;
                    count++;
                }
            }
        }
        return count == 0 ? 0.0 : (double) sum / count;
    }

    private void processBrightness(double brightness, long timestampNs) {
        lastBrightnessValue = brightness;
        if (brightnessBaseline < 0) {
            brightnessBaseline = brightness;
            lastBrightness = brightness;
            return;
        }

        double triggerThreshold = Math.max(155.0, brightnessBaseline + 42.0);
        boolean sharpRise = lastBrightness > 0 && brightness > lastBrightness + 22.0;
        boolean refractoryPassed = lastFlashNs == Long.MIN_VALUE
                || timestampNs - lastFlashNs > EVENT_REFRACTORY_NS;

        if (flashArmed && refractoryPassed && brightness >= triggerThreshold && sharpRise) {
            flashArmed = false;
            lastFlashNs = timestampNs;
            registerFlash(timestampNs);
        }

        if (!flashArmed && brightness < brightnessBaseline + 22.0) {
            flashArmed = true;
        }

        if (brightness < brightnessBaseline + 28.0) {
            brightnessBaseline = brightnessBaseline * 0.985 + brightness * 0.015;
        }
        lastBrightness = brightness;
        maybeRefreshDiagnostics();
    }

    private void startAudioCapture() {
        audioThread = new Thread(this::audioLoop, "AVSyncAudio");
        audioThread.start();
    }

    private void audioLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_AUDIO);
        int minBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        int bufferBytes = Math.max(minBuffer * 2, 8192);
        try {
            audioRecord = createAudioRecord(MediaRecorder.AudioSource.UNPROCESSED, bufferBytes);
            if (audioRecord.getState() != AudioRecord.STATE_INITIALIZED) {
                audioRecord.release();
                audioRecord = createAudioRecord(MediaRecorder.AudioSource.MIC, bufferBytes);
            }
            if (audioRecord.getState() != AudioRecord.STATE_INITIALIZED) {
                showError("Mikrofon se nepodařilo inicializovat.");
                return;
            }

            short[] samples = new short[480];
            AudioTimestamp audioTimestamp = new AudioTimestamp();
            long totalFrames = 0;
            boolean audioArmed = true;
            audioRecord.startRecording();

            while (measuring && !Thread.currentThread().isInterrupted()) {
                int read = audioRecord.read(samples, 0, samples.length, AudioRecord.READ_BLOCKING);
                if (read <= 0) continue;
                long blockStartFrame = totalFrames;
                totalFrames += read;

                double sumSquares = 0.0;
                int peak = 0;
                for (int i = 0; i < read; i++) {
                    int value = Math.abs((int) samples[i]);
                    peak = Math.max(peak, value);
                    sumSquares += (double) value * value;
                }
                double rms = Math.sqrt(sumSquares / read);
                lastAudioRms = rms;
                double rmsThreshold = Math.max(1100.0, noiseRms * 4.2);
                int peakThreshold = (int) Math.max(4500.0, noiseRms * 8.0);

                if (audioArmed && rms >= rmsThreshold && peak >= peakThreshold) {
                    int onset = findAudioOnset(samples, read, peakThreshold);
                    long eventFrame = blockStartFrame + onset;
                    long eventNs = audioFrameToTimeNs(audioRecord, audioTimestamp, eventFrame, read, onset);
                    boolean refractoryPassed = lastAudioNs == Long.MIN_VALUE
                            || eventNs - lastAudioNs > EVENT_REFRACTORY_NS;
                    if (refractoryPassed) {
                        lastAudioNs = eventNs;
                        audioArmed = false;
                        registerAudio(eventNs);
                    }
                }

                if (!audioArmed && rms < Math.max(750.0, noiseRms * 2.0)) {
                    audioArmed = true;
                }
                if (audioArmed && rms < noiseRms * 2.5) {
                    noiseRms = noiseRms * 0.99 + rms * 0.01;
                }
                maybeRefreshDiagnostics();
            }
        } catch (SecurityException | IllegalArgumentException | IllegalStateException e) {
            showError("Chyba mikrofonu: " + e.getMessage());
        } finally {
            if (audioRecord != null) {
                try {
                    audioRecord.stop();
                } catch (Exception ignored) {
                }
                audioRecord.release();
                audioRecord = null;
            }
        }
    }

    private AudioRecord createAudioRecord(int source, int bufferBytes) {
        return new AudioRecord.Builder()
                .setAudioSource(source)
                .setAudioFormat(new AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build())
                .setBufferSizeInBytes(bufferBytes)
                .build();
    }

    private int findAudioOnset(short[] samples, int count, int peakThreshold) {
        int threshold = Math.max(1800, peakThreshold / 2);
        for (int i = 0; i < count; i++) {
            if (Math.abs((int) samples[i]) >= threshold) return i;
        }
        return 0;
    }

    private long audioFrameToTimeNs(AudioRecord record, AudioTimestamp timestamp,
                                    long eventFrame, int readCount, int onset) {
        int result = record.getTimestamp(timestamp, AudioTimestamp.TIMEBASE_MONOTONIC);
        if (result == AudioRecord.SUCCESS) {
            double frameDelta = (double) eventFrame - timestamp.framePosition;
            return timestamp.nanoTime + Math.round(frameDelta * 1_000_000_000.0 / SAMPLE_RATE);
        }
        long now = System.nanoTime();
        long samplesBeforeEventFromEnd = readCount - onset;
        return now - Math.round(samplesBeforeEventFromEnd * 1_000_000_000.0 / SAMPLE_RATE);
    }

    private void stopAudioCapture() {
        if (audioRecord != null) {
            try {
                audioRecord.stop();
            } catch (Exception ignored) {
            }
        }
        if (audioThread != null) {
            audioThread.interrupt();
            try {
                audioThread.join(1000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            audioThread = null;
        }
    }

    private void registerFlash(long timestampNs) {
        synchronized (detectionLock) {
            flashCount++;
            flashEvents.addLast(timestampNs);
            pairEventsLocked();
        }
        runOnUiThread(this::updateUi);
    }

    private void registerAudio(long timestampNs) {
        synchronized (detectionLock) {
            audioCount++;
            audioEvents.addLast(timestampNs);
            pairEventsLocked();
        }
        runOnUiThread(this::updateUi);
    }

    private void pairEventsLocked() {
        while (!flashEvents.isEmpty() && !audioEvents.isEmpty()) {
            long flash = flashEvents.peekFirst();
            Long bestAudio = null;
            long bestDistance = Long.MAX_VALUE;
            for (Long audio : audioEvents) {
                long distance = Math.abs(audio - flash);
                if (distance < bestDistance) {
                    bestDistance = distance;
                    bestAudio = audio;
                }
            }

            if (bestAudio != null && bestDistance <= MAX_PAIR_DISTANCE_NS) {
                flashEvents.removeFirst();
                audioEvents.remove(bestAudio);
                double rawDelayMs = (bestAudio - flash) / 1_000_000.0;
                double propagationMs = distanceMeters / 343.0 * 1000.0;
                measurementsMs.add(rawDelayMs - propagationMs);
                if (measurementsMs.size() > 30) measurementsMs.remove(0);
                continue;
            }

            long firstAudio = audioEvents.peekFirst();
            if (firstAudio < flash - MAX_PAIR_DISTANCE_NS) {
                audioEvents.removeFirst();
            } else if (flash < firstAudio - MAX_PAIR_DISTANCE_NS) {
                flashEvents.removeFirst();
            } else {
                break;
            }
        }
    }

    private void maybeRefreshDiagnostics() {
        long now = System.nanoTime();
        if ((now / 250_000_000L) != ((now - 20_000_000L) / 250_000_000L)) {
            runOnUiThread(this::updateDiagnostics);
        }
    }

    private void updateUi() {
        List<Double> values;
        int flashes;
        int tones;
        synchronized (detectionLock) {
            values = new ArrayList<>(measurementsMs);
            flashes = flashCount;
            tones = audioCount;
        }

        countersView.setText(String.format(Locale.getDefault(),
                "Detekováno: záblesky %d  •  pípnutí %d  •  spárovaná měření %d",
                flashes, tones, values.size()));

        if (values.isEmpty()) {
            resultView.setText("Zatím žádné spárované měření");
        } else {
            double latest = values.get(values.size() - 1);
            double mean = mean(values);
            double median = median(values);
            double deviation = standardDeviation(values, mean);
            String direction;
            if (Math.abs(median) < 5.0) {
                direction = "Zvuk a obraz jsou prakticky současně";
            } else if (median > 0) {
                direction = String.format(Locale.getDefault(), "Zvuk je o %.0f ms PO obrazu", median);
            } else {
                direction = String.format(Locale.getDefault(), "Zvuk je o %.0f ms PŘED obrazem", Math.abs(median));
            }
            String confidence = values.size() < 4 ? "orientační"
                    : deviation <= 10.0 ? "vysoká"
                    : deviation <= 25.0 ? "střední" : "nízká";
            resultView.setText(String.format(Locale.getDefault(),
                    "%s\n\nMedián: %+.1f ms\nPrůměr: %+.1f ms\nPoslední: %+.1f ms\nRozptyl: ±%.1f ms\nSpolehlivost: %s",
                    direction, median, mean, latest, deviation, confidence));
        }
        updateDiagnostics();
    }

    private void updateDiagnostics() {
        String cameraClock = cameraTimestampRealtime ? "senzorový čas MONOTONIC" : "náhradní čas callbacku";
        diagnosticsView.setText(String.format(Locale.getDefault(),
                "Diagnostika: jas %.1f (základ %.1f)  •  audio RMS %.0f (šum %.0f)\nČas kamery: %s  •  korekce vzdálenosti %.1f ms",
                lastBrightnessValue, Math.max(0.0, brightnessBaseline), lastAudioRms, noiseRms,
                cameraClock, distanceMeters / 343.0 * 1000.0));
    }

    private double mean(List<Double> values) {
        double sum = 0.0;
        for (double value : values) sum += value;
        return sum / values.size();
    }

    private double median(List<Double> values) {
        List<Double> sorted = new ArrayList<>(values);
        Collections.sort(sorted);
        int middle = sorted.size() / 2;
        return sorted.size() % 2 == 0
                ? (sorted.get(middle - 1) + sorted.get(middle)) / 2.0
                : sorted.get(middle);
    }

    private double standardDeviation(List<Double> values, double mean) {
        if (values.size() < 2) return 0.0;
        double sum = 0.0;
        for (double value : values) {
            double delta = value - mean;
            sum += delta * delta;
        }
        return Math.sqrt(sum / (values.size() - 1));
    }

    private void closeCamera() {
        if (captureSession != null) {
            captureSession.close();
            captureSession = null;
        }
        if (cameraDevice != null) {
            cameraDevice.close();
            cameraDevice = null;
        }
        if (imageReader != null) {
            imageReader.close();
            imageReader = null;
        }
        if (previewSurface != null) {
            previewSurface.release();
            previewSurface = null;
        }
    }

    private void showError(String message) {
        runOnUiThread(() -> {
            statusView.setText(message);
            Toast.makeText(this, message, Toast.LENGTH_LONG).show();
        });
    }

    @Override
    public void onSurfaceTextureAvailable(SurfaceTexture surface, int width, int height) {
        if (measuring) openCamera();
    }

    @Override
    public void onSurfaceTextureSizeChanged(SurfaceTexture surface, int width, int height) {
    }

    @Override
    public boolean onSurfaceTextureDestroyed(SurfaceTexture surface) {
        closeCamera();
        return true;
    }

    @Override
    public void onSurfaceTextureUpdated(SurfaceTexture surface) {
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (measuring) stopMeasurement();
    }

    private static class TargetOverlay extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

        TargetOverlay(Context context) {
            super(context);
            paint.setColor(Color.WHITE);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(4f * getResources().getDisplayMetrics().density);
            paint.setAlpha(210);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            float left = getWidth() * 0.25f;
            float right = getWidth() * 0.75f;
            float top = getHeight() * 0.25f;
            float bottom = getHeight() * 0.75f;
            canvas.drawRect(left, top, right, bottom, paint);
            canvas.drawLine(getWidth() / 2f - 20, getHeight() / 2f,
                    getWidth() / 2f + 20, getHeight() / 2f, paint);
            canvas.drawLine(getWidth() / 2f, getHeight() / 2f - 20,
                    getWidth() / 2f, getHeight() / 2f + 20, paint);
        }
    }
}
