fn main() {
    // Link the same libportaudio that PyAudio uses (macOS: `brew install portaudio`).
    if let Err(e) = pkg_config::Config::new().probe("portaudio-2.0") {
        panic!("PortAudio not found via pkg-config: {e}");
    }
}
