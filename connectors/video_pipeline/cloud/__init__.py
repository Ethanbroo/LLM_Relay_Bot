"""Cloud rendering infrastructure for the video pipeline.

Provides scalable rendering by distributing frame composition across
multiple workers — either locally (multiprocessing) or in the cloud
(AWS Lambda, GCP Cloud Run).

Architecture:
- RenderJob: Describes a chunk of frames to render
- RenderResult: The output from rendering a chunk
- AbstractRenderProvider: Base class for all render backends
- LocalMultiprocessProvider: Uses multiprocessing.Pool on the local machine
- LambdaRenderProvider: Distributes chunks to AWS Lambda functions
- CloudRunRenderProvider: Distributes chunks to GCP Cloud Run instances
- RenderOrchestrator: Splits timeline into chunks, dispatches to provider,
                      reassembles the final video

The existing FrameCompositor is stateless by design — given a timeline and
frame number, it produces exactly one frame. This makes it trivially
parallelizable: each worker gets a chunk of frame numbers, renders them
independently, and returns the encoded chunk. The orchestrator concatenates
chunks into the final video.
"""

__version__ = "0.1.0"
