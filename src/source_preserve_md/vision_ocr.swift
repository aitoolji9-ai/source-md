import Foundation
import Vision
import ImageIO

// File OCR only: no screen capture, UI automation, network or language-model API.
do {
    let arguments = Array(CommandLine.arguments.dropFirst())
    let languageArguments = arguments.filter { $0.hasPrefix("--language=") }
    let language = languageArguments.first.map { String($0.dropFirst("--language=".count)) }
    guard languageArguments.count <= 1, language == nil || ["ko-KR", "en-US"].contains(language!) else {
        throw NSError(domain: "SourcePreserveMD", code: 3,
                      userInfo: [NSLocalizedDescriptionKey: "Unsupported OCR language"])
    }
    let paths = arguments.filter { $0 != "--language-correction" && !$0.hasPrefix("--language=") }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = arguments.contains("--language-correction")
    let supported = try request.supportedRecognitionLanguages()
    request.recognitionLanguages = (language.map { [$0] } ?? ["ko-KR", "en-US"]).filter { supported.contains($0) }
    guard !request.recognitionLanguages.isEmpty else {
        throw NSError(domain: "SourcePreserveMD", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Korean/English recognition unavailable"])
    }
    var output: [String: Any] = ["engine": "apple-vision", "revision": request.revision,
        "languages": request.recognitionLanguages, "supported_languages": supported,
        "language_correction": request.usesLanguageCorrection, "recognition_level": "accurate"]
    if paths == ["--info"] {
        output["observations"] = []
    } else {
        guard paths.count == 1,
              let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: paths[0]) as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw NSError(domain: "SourcePreserveMD", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "Cannot decode OCR input image"])
        }
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        output["width"] = image.width
        output["height"] = image.height
        output["observations"] = (request.results ?? []).compactMap { observation -> [String: Any]? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            // Normalized top-left coordinates, independent of image resolution.
            return ["text": candidate.string, "confidence": candidate.confidence,
                    "bbox": [box.minX, 1 - box.maxY, box.width, box.height]]
        }
    }
    let encoded = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    FileHandle.standardOutput.write(encoded)
} catch {
    FileHandle.standardError.write(Data(error.localizedDescription.utf8))
    exit(2)
}
