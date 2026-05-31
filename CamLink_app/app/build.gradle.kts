import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.tasks.KotlinCompile

plugins {
    alias(libs.plugins.android.application)
    // Necesario porque el módulo es Kotlin (.kt) y abajo usamos los bloques
    // `kotlin {}` y configuramos KotlinCompile. AGP 8.x exige declararlo.
    alias(libs.plugins.kotlin.android)
}
android {
    namespace = "com.ipn.mx.onvif"
    // compileSdk 35 = Android 15 (Vanilla Ice Cream). Habilita APIs nuevas
    // y permite usar las últimas dependencias AndroidX (core-ktx 1.15+,
    // material 1.13+, lifecycle 2.8.7+, etc.).
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ipn.mx.onvif"
        minSdk = 29
        // targetSdk 35 = comportamiento Android 15. Recomendado por Google Play.
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        // sourceCompatibility/targetCompatibility = BYTECODE Java que produce
        // javac (Android lo aplana en DEX). Subido a 17 para coincidir con el
        // jvmTarget de Kotlin (ver bloque kotlin{} abajo) — Gradle exige que
        // ambos sean iguales o falla con "Inconsistent JVM-target compatibility".
        //
        // Java 17 es seguro con minSdk=29 (Android 10+) y AGP 8.x lo soporta
        // sin coreLibraryDesugaring para nuestras dependencias.
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        viewBinding = false
        compose = false
    }
}

kotlin {
    // jvmToolchain = JDK que Gradle usa para compilar Y para ejecutar tests.
    // JDK 8 fallaba en testDebugUnitTest con "Unrecognized option --add-opens"
    // (esa opción es JDK 9+). AGP 8.x + tests de Android requieren JDK 17.
    jvmToolchain(17)
}

// Forzar explícitamente el jvmTarget de cada KotlinCompile a 17. Sin esta
// línea, en algunos setups (cache de daemon, JDK del sistema≠toolchain) el
// task de Kotlin compila a JVM 17 pero el de Java a JVM 1.8 y Gradle aborta
// con "Inconsistent JVM-target compatibility". Esto deja ambos en 17 sin
// depender de heurísticas del toolchain.
tasks.withType<KotlinCompile>().configureEach {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    // ─ AndroidX core (versiones que requieren compileSdk 35) ─
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.1")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")

    // Navigation 2.8.x (la 2.9.x exige Kotlin 2.1+; estamos en 2.0.21)
    val navVersion = "2.8.5"
    implementation("androidx.navigation:navigation-fragment-ktx:$navVersion")
    implementation("androidx.navigation:navigation-ui-ktx:$navVersion")

    // Lifecycle 2.8.7 (última de la rama estable compatible con Kotlin 2.0)
    val lifecycleVersion = "2.8.7"
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:$lifecycleVersion")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:$lifecycleVersion")

    // CameraX 1.4.x (estable para compileSdk 35)
    val cameraXVersion = "1.4.1"
    implementation("androidx.camera:camera-core:$cameraXVersion")
    implementation("androidx.camera:camera-camera2:$cameraXVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraXVersion")
    implementation("androidx.camera:camera-view:$cameraXVersion")

    // ML Kit (QR)
    implementation("com.google.mlkit:barcode-scanning:17.3.0")

    // ExoPlayer / Media3 1.5.x (compat con minSdk 29)
    val media3Version = "1.5.1"
    implementation("androidx.media3:media3-exoplayer:$media3Version")
    implementation("androidx.media3:media3-exoplayer-rtsp:$media3Version")
    // HLS: transporte preferido para el directo en móvil (ExoPlayer es muy
    // fiable con HLS; RTSP queda como fallback). go2rtc sirve el HLS.
    implementation("androidx.media3:media3-exoplayer-hls:$media3Version")
    implementation("androidx.media3:media3-ui:$media3Version")

    // Retrofit + OkHttp
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}