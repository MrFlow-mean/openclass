import AppKit
import Foundation
import PDFKit
import Vision

struct OCRLine: Codable {
    let text: String
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let page: Int?
}

struct OCRPayload: Codable {
    let text: String
    let lines: [OCRLine]
}

enum VisionOCRError: Error {
    case missingPath
    case cannotLoadImage
    case cannotBuildCGImage
    case cannotLoadPDF
    case cannotRenderPDFPage
}

func encodeAndPrint(_ payload: OCRPayload) throws {
    let encoder = JSONEncoder()
    let data = try encoder.encode(payload)
    guard let text = String(data: data, encoding: .utf8) else {
        return
    }
    print(text)
}

func cgImage(from image: NSImage) throws -> CGImage {
    var rect = NSRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw VisionOCRError.cannotBuildCGImage
    }
    return cgImage
}

func recognizeText(from cgImage: CGImage) throws -> [OCRLine] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])

    return (request.results ?? []).compactMap { result in
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
            y: Double(box.midY),
            width: Double(box.width),
            height: Double(box.height),
            page: nil
        )
    }
}

func orderedTextLines(_ lines: [OCRLine]) -> [OCRLine] {
    let xSorted = lines.sorted { left, right in
        if abs(left.x - right.x) > 0.001 {
            return left.x < right.x
        }
        return left.y > right.y
    }
    var largestGap = 0.0
    var splitX: Double?
    if xSorted.count >= 10 {
        for index in 1..<xSorted.count {
            let gap = xSorted[index].x - xSorted[index - 1].x
            if gap > largestGap {
                largestGap = gap
                splitX = (xSorted[index].x + xSorted[index - 1].x) / 2
            }
        }
    }

    if let splitX, largestGap >= 0.18 {
        let leftColumn = lines.filter { $0.x <= splitX }
        let rightColumn = lines.filter { $0.x > splitX }
        if leftColumn.count >= 4 && rightColumn.count >= 4 {
            func topToBottom(_ column: [OCRLine]) -> [OCRLine] {
                column.sorted { left, right in
                    if abs(left.y - right.y) > 0.012 {
                        return left.y > right.y
                    }
                    return left.x < right.x
                }
            }
            return topToBottom(leftColumn) + topToBottom(rightColumn)
        }
    }

    let ordered = lines.sorted { left, right in
        if abs(left.y - right.y) > 0.012 {
            return left.y > right.y
        }
        return left.x < right.x
    }
    return ordered
}

func renderPDFPage(_ page: PDFPage) throws -> CGImage {
    let bounds = page.bounds(for: .mediaBox)
    let maxSide: CGFloat = 2200
    let scale = maxSide / max(bounds.width, bounds.height)
    let size = NSSize(width: max(bounds.width * scale, 1), height: max(bounds.height * scale, 1))
    let image = page.thumbnail(of: size, for: .mediaBox)
    guard image.size.width > 0 && image.size.height > 0 else {
        throw VisionOCRError.cannotRenderPDFPage
    }
    return try cgImage(from: image)
}

do {
    let arguments = CommandLine.arguments
    guard arguments.count >= 2 else {
        throw VisionOCRError.missingPath
    }

    let fileURL = URL(fileURLWithPath: arguments[1])
    if fileURL.pathExtension.lowercased() == "pdf" {
        guard let document = PDFDocument(url: fileURL) else {
            throw VisionOCRError.cannotLoadPDF
        }

        let requestedStart = max(Int(arguments.dropFirst(2).first ?? "1") ?? 1, 1)
        let requestedEnd = max(Int(arguments.dropFirst(3).first ?? "\(requestedStart)") ?? requestedStart, requestedStart)
        let maxPages = max(Int(arguments.dropFirst(4).first ?? "4") ?? 4, 1)
        let startPage = min(requestedStart, document.pageCount)
        let endPage = min(requestedEnd, document.pageCount)

        var textLines: [OCRLine] = []
        var processedPages = 0
        if startPage <= endPage {
            for pageNumber in startPage...endPage {
                if processedPages >= maxPages {
                    break
                }
                guard let page = document.page(at: pageNumber - 1) else {
                    continue
                }
                let pageLines = try orderedTextLines(recognizeText(from: renderPDFPage(page))).map { line in
                    OCRLine(
                        text: line.text,
                        x: line.x,
                        y: line.y,
                        width: line.width,
                        height: line.height,
                        page: pageNumber
                    )
                }
                if !pageLines.isEmpty {
                    textLines.append(contentsOf: pageLines)
                }
                processedPages += 1
            }
        }

        try encodeAndPrint(
            OCRPayload(
                text: textLines.map(\.text).joined(separator: "\n"),
                lines: textLines
            )
        )
        exit(0)
    }

    guard let image = NSImage(contentsOf: fileURL) else {
        throw VisionOCRError.cannotLoadImage
    }

    let textLines = try orderedTextLines(recognizeText(from: cgImage(from: image)))
    try encodeAndPrint(
        OCRPayload(
            text: textLines.map(\.text).joined(separator: "\n"),
            lines: textLines
        )
    )
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(1)
}
