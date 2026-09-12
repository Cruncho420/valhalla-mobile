import com.vanniktech.maven.publish.AndroidSingleVariantLibrary
import com.vanniktech.maven.publish.SonatypeHost
import java.io.File
import java.net.URI
import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.tasks.KotlinCompile

plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.jetbrains.kotlin.android)
    alias(libs.plugins.ktfmt)
    alias(libs.plugins.mavenPublish)
    alias(libs.plugins.dokka)
}

android {
    namespace = "com.valhalla.valhalla"
    compileSdk = 34
    // Keep AGP packaging on the same explicit NDK used by the native build and verifier.
    System.getenv("ANDROID_NDK_HOME")?.let { selectedNdk ->
        val properties = Properties()
        file("$selectedNdk/source.properties").inputStream().use { properties.load(it) }
        val selectedVersion = properties.getProperty("Pkg.Revision")?.trim()
        require(!selectedVersion.isNullOrEmpty()) { "Selected NDK has no package revision" }
        ndkPath = selectedNdk
        ndkVersion = selectedVersion
    }

    defaultConfig {
        minSdk = 26
        // The standalone instrumentation APK must not inherit minSdk as its target.
        targetSdk = 34

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        consumerProguardFiles("consumer-rules.pro")
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
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

tasks.withType<KotlinCompile>().configureEach {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

// CI must verify the final instrumented APK before the connected task can install it.
System.getenv("VALHALLA_TRACE_AAR")?.let { traceAar ->
    val verifyTraceApk = tasks.register<Exec>("verifyValhallaTraceApk") {
        dependsOn("packageDebugAndroidTest")
        doFirst {
            val repo = rootProject.projectDir.parentFile
            val ndk = System.getenv("ANDROID_NDK_HOME") ?: error("Trace verification requires NDK")
            val stripTools = file("$ndk/toolchains/llvm/prebuilt").listFiles()
                ?.map { File(it, "bin/llvm-strip") }?.filter { it.isFile } ?: emptyList()
            require(stripTools.size == 1) { "Expected exactly one selected NDK strip tool" }
            val apks = project.layout.buildDirectory.dir("outputs/apk/androidTest/debug").get()
                .asFile.listFiles()?.filter { it.isFile && it.extension == "apk" } ?: emptyList()
            require(apks.size == 1) { "Expected exactly one trace instrumentation APK" }
            val proof = System.getenv("VALHALLA_TRACE_AAR_RECEIPT")
                ?: error("Trace verification requires the AAR receipt")
            val output = System.getenv("VALHALLA_TRACE_APK_RECEIPT")
                ?: error("Trace verification requires an APK receipt output")
            commandLine("python3", File(repo, "scripts/verify_android_test_apk.py").absolutePath,
                "--repo", repo.absolutePath, "--aar", traceAar, "--aar-receipt", proof,
                "--apk", apks.single().absolutePath, "--strip-tool", stripTools.single().absolutePath,
                "--output", output)
        }
    }
    // AGP registers the connected task after this script; configure lazily.
    tasks.matching { it.name == "connectedDebugAndroidTest" }.configureEach {
        dependsOn(verifyTraceApk)
    }
}

dokka {
    dokkaPublications.html {
        outputDirectory.set(layout.buildDirectory.dir("docs"))
    }

    dokkaSourceSets.main {
        moduleName.set("Valhalla Mobile")

        sourceLink {
            localDirectory.set(file("src/main/kotlin"))
            remoteUrl.set(URI("https://github.com/Rallista/valhalla-mobile"))
            remoteLineSuffix.set("#L")
        }

        includes.from(
            fileTree("docs") {
                include("**/*.md")
            }
        )
    }
}

dependencies {
    implementation(libs.core.ktx)

    implementation(libs.moshi.kotlin)
    implementation(libs.moshi.adapters)

    implementation(libs.valhalla.models.api)
    implementation(libs.valhalla.models.config)
    implementation(libs.osrm.api)

    testImplementation(libs.junit)

    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.core)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.rules)
}

val archs = listOf("arm64-v8a", "armeabi-v7a", "x86_64", "x86")

// Define a custom task to run the shell script
archs.forEach { arch ->
    tasks.register<Exec>("buildValhallaFor-${arch}") {
        description = "Build libValhalla for $arch architecture"
        group = "build"

        // Change the working door to the repository root.
        workingDir = file("${project.projectDir}/../../")
        environment("VCPKG_ROOT", "${workingDir.absolutePath}/vcpkg")

        commandLine("bash", "./build.sh", "--android", arch)

        onlyIf {
            // Existing JNI bytes need independent source and ABI proof before reuse.
            val nativeRoot = workingDir.absolutePath
            val verification = project.exec {
                commandLine(
                    "python3",
                    "$nativeRoot/scripts/verify_native_prebuilt.py",
                    "--repo", nativeRoot,
                    "--abi", arch,
                )
                isIgnoreExitValue = true
            }.exitValue
            when (verification) {
                0 -> false
                10 -> true
                else -> throw GradleException("Native prebuilt provenance verification failed for $arch")
            }
        }
    }
}

tasks.named("preBuild") {
    // Efficiently build any architecture that doesn't exist in jniLibs.
    dependsOn("buildValhallaFor-arm64-v8a")
    dependsOn("buildValhallaFor-armeabi-v7a")
    dependsOn("buildValhallaFor-x86_64")
    dependsOn("buildValhallaFor-x86")
}

mavenPublishing {
    publishToMavenCentral(SonatypeHost.CENTRAL_PORTAL)
    signAllPublications()

    if (project.version.toString() === "unspecified") {
        throw IllegalArgumentException("Version must be specified")
    }

    coordinates("io.github.rallista", "valhalla-mobile", project.version.toString())

    configure(AndroidSingleVariantLibrary(sourcesJar = true, publishJavadocJar = true))

    pom {
        name.set("Valhalla Mobile")
        url.set("https://github.com/Rallista/valhalla-mobile")
        description.set("A mobile app focused wrapper library for the valhalla routing engine")
        inceptionYear.set("2024")
        licenses {
            license {
                name.set("MIT")
                url.set("https://github.com/Rallista/valhalla-mobile?tab=MIT-1-ov-file#MIT-1-ov-file")
            }
        }
        developers {
            developer {
                name.set("Jacob Fielding")
                organization.set("Rallista")
                organizationUrl.set("https://rallista.app")
            }
        }
        contributors {
            contributor {
                name.set("Valhalla")
                organizationUrl.set("https://github.com/valhalla/valhalla")
            }
        }
        scm {
            connection.set("scm:git:https://github.com/Rallista/valhalla-mobile.git")
            developerConnection.set("scm:git:ssh://github.com/Rallista/valhalla-mobile.git")
            url.set("https://github.com/Rallista/valhalla-mobile")
        }
    }
}
