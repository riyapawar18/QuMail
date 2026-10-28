import React, { useEffect } from "react";
import { motion } from "framer-motion";

export default function SplashAnimation({ onFinish = () => {}, durationMs = 2600 }) {
  useEffect(() => {
    const t = setTimeout(onFinish, durationMs);
    return () => clearTimeout(t);
  }, [onFinish, durationMs]);

  return (
    <div className="fixed inset-0 bg-[#DCDCDC] flex flex-col items-center justify-center z-[9999]">
      <div className="flex flex-col items-center">
        {/* Animated Logo Box */}
        <motion.div 
          initial={{ scale: 0.8, opacity: 0, rotate: -10 }}
          animate={{ scale: 1, opacity: 1, rotate: 0 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
          className="relative mb-8 flex items-center justify-center"
        >
          <div className="absolute inset-0 bg-blue-500 rounded-[32px] blur-3xl opacity-20 animate-pulse"></div>
          <img 
            src="/logo.png" 
            alt="QuMail Logo" 
            className="relative w-28 h-28 object-contain drop-shadow-2xl mix-blend-multiply z-10" 
          />
        </motion.div>

        {/* Text and Progress */}
        <motion.div
           initial={{ opacity: 0, y: 10 }}
           animate={{ opacity: 1, y: 0 }}
           transition={{ delay: 0.3, duration: 0.6 }}
           className="flex flex-col items-center"
        >
          <h1 className="text-3xl font-black tracking-tight text-gray-900 mb-8 italic">QuMail</h1>
          
          <div className="w-48 h-1 bg-gray-200 rounded-full overflow-hidden relative shadow-inner">
            <motion.div 
              initial={{ width: "0%" }}
              animate={{ width: "100%" }}
              transition={{ duration: 2.2, ease: "easeInOut" }}
              className="absolute inset-y-0 left-0 bg-[#1A73E8] shadow-[0_0_10px_#1A73E8]"
            />
          </div>
          
          <motion.p 
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.6 }}
            transition={{ delay: 1, duration: 1 }}
            className="mt-6 text-[12px] font-bold text-gray-500 uppercase tracking-widest"
          >
            Securing your connection...
          </motion.p>
        </motion.div>
      </div>

      {/* Footer Branding */}
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.4 }}
        transition={{ delay: 1.5, duration: 0.8 }}
        className="absolute bottom-12 flex flex-col items-center gap-2"
      >
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-blue-600"></div>
          <span className="text-[11px] font-black text-gray-900 tracking-tighter uppercase italic">Quantum Safe Encryption</span>
        </div>
      </motion.div>
    </div>
  );
}