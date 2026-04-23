import AppKit
import Foundation
import Vision

struct OCRLine: Codable {
    let text: String
    let x: Double
    let y: Double
}

struct OCRPayload: Codable {
    let text: String
    let lines: [String]
}

enum VisionOCRError: Error {
    case missingPath
    case cannotLoadImage
    case cannotBuildCGImage
}

func encodeAndPrint(_ payload: OCRPayload) throws {
    let encoder = JSONEncoder()
    let data = try encoder.encode(payload)
    guard let text = String(data: data, encoding: .utf8) else {
        return
    }
    print(text)
}

do {
    let arguments = CommandLine.arguments
    guard arguments.count >= 2 else {
        throw VisionOCRError.missingPath
    }

    let fileURL = URL(fileURLWithPath: arguments[1])
    guard let image = NSImage(contentsOf: fileURL) else {
        throw VisionOCRError.cannotLoadImage
    }

    var rect = NSRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw VisionOCRError.cannotBuildCGImage
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])

    let lines: [OCRLine] = (request.results ?? []).compactMap { result in
        guard let observation = result as? VNRecognizedTextObservation else {
            return nil
        }
        guard let candidate = observation.topCandidates(1).first else {
            return nil
        }

        let box = observation.boundingBox
        return OCRLine(
            text: candidate.string,
            x: Double(box.minX),
            y: Double(box.midY)
        )
    }

    let ordered = lines.sorted { left, right in
        if abs(left.y - right.y) > 0.04 {
            return left.y > right.y
        }
        return left.x < right.x
    }

    let textLines = ordered.map(\.text)
    try encodeAndPrint(
        OCRPayload(
            text: textLines.joined(separator: "\n"),
            lines: textLines
        )
    )
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(1)
}
