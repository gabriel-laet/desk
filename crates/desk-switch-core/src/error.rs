use std::fmt;
use std::process::ExitCode;

/// Recoverable CLI failure. `Exit` maps to a non-zero process status.
#[derive(Debug)]
pub enum Error {
    Exit { message: String, code: i32 },
    Interrupted,
    Io(std::io::Error),
}

impl Error {
    pub fn exit(message: impl Into<String>) -> Self {
        Self::Exit {
            message: message.into(),
            code: 1,
        }
    }

    pub fn usage(message: impl Into<String>) -> Self {
        Self::Exit {
            message: message.into(),
            code: 2,
        }
    }

    pub fn code(&self) -> i32 {
        match self {
            Self::Exit { code, .. } => *code,
            Self::Interrupted => 130,
            Self::Io(_) => 1,
        }
    }

    pub fn exit_code(&self) -> ExitCode {
        ExitCode::from(self.code().clamp(0, 255) as u8)
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Exit { message, .. } => write!(f, "{message}"),
            Self::Interrupted => write!(f, "interrupted"),
            Self::Io(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(value: std::io::Error) -> Self {
        Self::Io(value)
    }
}

pub type Result<T> = std::result::Result<T, Error>;
